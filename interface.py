from __future__ import annotations

import ctypes
import json
import os
import re
import threading
import time
import traceback
from functools import wraps
from pathlib import Path

import webview

from backend.controller import SAPController
from backend.auth import (
    autenticar_usuario as autenticar_usuario_compartilhado,
    cadastrar_usuario as cadastrar_usuario_compartilhado,
    encerrar_acesso_lembrado,
    recuperar_acesso as recuperar_acesso_compartilhado,
    verificar_acesso_lembrado,
)
from backend.documentos import analisar_doc, limpar_doc
from backend.flows.cliente_cadastro import (
    adicionar_setores_cliente,
    criar_cliente,
    criar_cliente_multa_contratual,
)
from backend.flows.extratos_caixa_chrome import (
    abrir_navegador_caixa,
    diagnosticar_chrome_caixa,
    executar_download_extrato_atual,
)
from backend.flows.saldo_contas_sap import executar_consulta_saldos_sap
from backend.drafts import (
    diagnostico_rascunhos,
    excluir_rascunho_cliente,
    listar_rascunhos_clientes,
    salvar_rascunho_cliente,
)
from backend.history import (
    diagnostico_historico,
    listar_historico_pedidos,
    obter_historico_pedido,
    registrar_historico_pedido,
    registrar_historico_saldo_contas,
)
from backend.logger import Logger
from backend.relatorios.saldo_contas import (
    ABA_MODELO_SALDO_CONTAS,
    analisar_planilha_saldo_contas as analisar_arquivo_saldo_contas,
    detectar_modelo_saldo_contas as detectar_modelo_arquivo_saldo_contas,
    planejar_consultas_saldo_contas,
    preencher_planilha_saldo_contas,
)
from backend.relatorios.saldo_contas_modelos import (
    excluir_modelo_saldo_contas as excluir_modelo_saldo_contas_salvo,
    listar_modelos_saldo_contas as listar_modelos_saldo_contas_salvos,
    obter_modelo_saldo_contas,
    salvar_modelo_saldo_contas as salvar_modelo_saldo_contas_local,
)
from backend.sap_connection import conectar_sap
from backend.utils.sap_waits import SapKeepAlive
from backend.support_mail import process_support_request
from backend.settings import SETTINGS, get_runtime_root, get_setting
from backend.validators import validar_dados, validar_dados_multa_contratual
from backend.valores import analisar_valor, analisar_valor_sem_limite


# Converte objetos Python para tipos seguros de enviar ao JavaScript.
def _serializar_para_front(valor):
    if isinstance(valor, Path):
        return str(valor)

    if isinstance(valor, dict):
        return {
            str(chave): _serializar_para_front(conteudo)
            for chave, conteudo in valor.items()
        }

    if isinstance(valor, (list, tuple, set)):
        return [_serializar_para_front(item) for item in valor]

    return valor


_EXTRATOS_PREFERENCIAS_ARQUIVO = "extratos_caixa.json"
_PASTA_MES_EXTRATO_RE = re.compile(r"^\d{2}\.[A-Z]{3}$", re.IGNORECASE)
_PERFIS_DESENVOLVEDOR = {
    "linkedin": "https://www.linkedin.com/in/augusto-taylor-425285364/",
    "github": "https://github.com/AugustoTaylor",
}


def _requer_autenticacao(metodo):
    @wraps(metodo)
    def protegido(self, *args, **kwargs):
        if get_setting("auth", "enabled", default=True) and not self._usuario_autenticado:
            return {
                "ok": False,
                "auth_required": True,
                "msg": "Faca login com sua matricula para continuar.",
            }
        return metodo(self, *args, **kwargs)

    return protegido


class _ConfirmacaoOperacional:
    def __init__(self):
        self._evento = threading.Event()
        self._resultado = None

    def concluir(self, resultado="confirmado"):
        self._resultado = resultado
        self._evento.set()

    def set(self):
        self.concluir()

    def wait(self, timeout=None):
        if not self._evento.wait(timeout):
            return False

        return self._resultado

    def is_set(self):
        return self._evento.is_set()


def _normalizar_pasta_base_extratos(caminho) -> Path:
    pasta = Path(str(caminho or "").strip()).expanduser()

    if not str(pasta):
        return pasta

    nome = pasta.name.upper()

    if nome == "CEF" and _PASTA_MES_EXTRATO_RE.fullmatch(pasta.parent.name):
        pasta = pasta.parent.parent.parent
    elif _PASTA_MES_EXTRATO_RE.fullmatch(nome) and pasta.parent.name.isdigit():
        pasta = pasta.parent.parent
    elif nome.isdigit() and len(nome) == 4 and pasta.parent.name.upper() == "EXTRATOS":
        pasta = pasta.parent

    return pasta


def _pasta_extratos_em_rede_corporativa(caminho) -> bool:
    pasta = Path(str(caminho or "").strip())
    texto = str(pasta)

    if texto.startswith("\\\\"):
        return True

    if not pasta.anchor:
        return False

    try:
        return int(ctypes.windll.kernel32.GetDriveTypeW(str(pasta.anchor))) == 4
    except Exception:
        return False


# API exposta ao frontend pelo pywebview: o JS chama estes metodos.
class API:
    def __init__(self):
        self._logger = Logger()
        self._controller = SAPController(self._logger)
        self._window = None
        self._ui_lock = threading.Lock()
        self._flow_lock = threading.Lock()
        self._sap_activity_lock = threading.Lock()
        self._flow_running = False
        self._usuario_autenticado = None
        self._extrato_running = False
        self._cancel_event = threading.Event()
        self._extrato_pause_event = threading.Event()
        self._last_progress = {}
        self._notice_lock = threading.Lock()
        self._notice_events = {}
        self._keep_alive = SapKeepAlive(
            intervalo=60,
            pode_executar=self._pode_executar_keep_alive,
            bloqueio_atividade=self._sap_activity_lock,
        )
        self._keep_alive.start()

    def set_window(self, window):
        self._window = window

    def verificar_login_lembrado(self, versao_app=""):
        if not get_setting("auth", "enabled", default=True):
            self._usuario_autenticado = {"matricula": "ACESSO-LIVRE"}
            return {"ok": True, "usuario": self._usuario_autenticado, "lembrado": False}
        try:
            resultado = verificar_acesso_lembrado(versao_app=str(versao_app or ""))
            if resultado.get("ok"):
                self._usuario_autenticado = resultado.get("usuario")
            return _serializar_para_front(resultado)
        except Exception as exc:
            self._usuario_autenticado = None
            return {
                "ok": False,
                "lembrado": False,
                "msg": f"Nao foi possivel verificar o acesso compartilhado: {exc}",
            }

    def autenticar_usuario(self, dados):
        try:
            payload = dict(dados or {})
            resultado = autenticar_usuario_compartilhado(
                payload.get("matricula"),
                payload.get("senha"),
                lembrar=bool(payload.get("lembrar")),
                versao_app=str(payload.get("versao_app") or ""),
            )
            self._usuario_autenticado = resultado.get("usuario")
            return _serializar_para_front(resultado)
        except Exception as exc:
            self._usuario_autenticado = None
            return {"ok": False, "msg": str(exc) or "Nao foi possivel entrar."}

    def cadastrar_usuario(self, dados):
        try:
            payload = dict(dados or {})
            resultado = cadastrar_usuario_compartilhado(
                payload.get("matricula"),
                payload.get("senha"),
                versao_app=str(payload.get("versao_app") or ""),
            )
            self._usuario_autenticado = None
            return _serializar_para_front(resultado)
        except Exception as exc:
            self._usuario_autenticado = None
            return {"ok": False, "msg": str(exc) or "Nao foi possivel cadastrar."}

    def recuperar_acesso(self, dados):
        try:
            payload = dict(dados or {})
            resultado = recuperar_acesso_compartilhado(
                payload.get("matricula"),
                payload.get("chave_recuperacao"),
                payload.get("nova_senha"),
                versao_app=str(payload.get("versao_app") or ""),
            )
            self._usuario_autenticado = None
            return _serializar_para_front(resultado)
        except Exception as exc:
            self._usuario_autenticado = None
            return {"ok": False, "msg": str(exc) or "Nao foi possivel recuperar o acesso."}

    def sair_usuario(self):
        self._usuario_autenticado = None
        return _serializar_para_front(encerrar_acesso_lembrado())

    def obter_usuario_atual(self):
        return {
            "ok": bool(self._usuario_autenticado),
            "usuario": _serializar_para_front(self._usuario_autenticado),
        }

    # Abre somente os perfis publicos previamente autorizados no navegador padrao.
    def abrir_perfil_desenvolvedor(self, rede):
        chave = str(rede or "").strip().lower()
        url = _PERFIS_DESENVOLVEDOR.get(chave)

        if not url:
            return {
                "success": False,
                "message": "Perfil externo não reconhecido.",
            }

        try:
            os.startfile(url)
            return {
                "success": True,
                "rede": chave,
                "url": url,
            }
        except Exception as exc:
            return {
                "success": False,
                "rede": chave,
                "url": url,
                "message": f"Não foi possível abrir o perfil: {exc}",
            }

    def stop(self):
        self._keep_alive.stop()

    def _pode_executar_keep_alive(self):
        with self._flow_lock:
            return not self._flow_running

    # Localiza a janela ativa do pywebview para emitir eventos ao frontend.
    def _get_window(self):
        if self._window is not None:
            return self._window

        try:
            if webview.windows:
                self._window = webview.windows[0]
                return self._window
        except Exception:
            return None

        return None

    # Executa JavaScript na tela sem derrubar o backend caso a UI esteja indisponível.
    def _evaluate_js_safe(self, script: str) -> bool:
        window = self._get_window()

        if window is None:
            return False

        try:
            with self._ui_lock:
                run_js = getattr(window, "run_js", None)

                if callable(run_js):
                    run_js(script)
                else:
                    window.evaluate_js(script)
            return True
        except Exception:
            return False

    def _evaluate_js_value(self, script: str, default=None):
        window = self._get_window()

        if window is None:
            return default

        try:
            with self._ui_lock:
                return window.evaluate_js(script)
        except Exception:
            return default

    def _monitorar_confirmacao_local(self, notice_id, confirmacao):
        notice_id_js = json.dumps(str(notice_id or ""))

        while not confirmacao.is_set():
            if self._cancel_event.is_set():
                confirmacao.concluir("cancelado")
                break

            resultado = self._evaluate_js_value(
                "window.consumirSinalAvisoOperacional"
                f" ? window.consumirSinalAvisoOperacional({notice_id_js})"
                " : null",
                default=None,
            )

            if resultado:
                confirmacao.concluir(str(resultado))
                break

            time.sleep(0.2)

        with self._notice_lock:
            atual = self._notice_events.get(str(notice_id or ""))

            if atual is confirmacao:
                self._notice_events.pop(str(notice_id or ""), None)

    def _emitir_reset_progresso(self):
        self._emitir_funcao_js("resetarProgresso")

    def _emitir_funcao_js(self, nome_funcao: str, *args) -> bool:
        payload_nome = json.dumps(str(nome_funcao or ""))
        payload_args = ", ".join(
            json.dumps(_serializar_para_front(arg), ensure_ascii=False)
            for arg in args
        )

        script = (
            "try {\n"
            f"  const fn = window[{payload_nome}];\n"
            '  if (typeof fn === "function") {\n'
            f"    fn({payload_args});\n"
            "  }\n"
            "} catch (e) {}\n"
        )

        return self._evaluate_js_safe(script)

    def _emitir_log(self, mensagem: str, classe: str = ""):
        self._emitir_funcao_js("log", str(mensagem or ""), str(classe or ""))

    def _emitir_status(self, texto: str, classe: str):
        self._emitir_funcao_js("setStatus", str(texto or ""), str(classe or ""))

    def _emitir_preencher_resultado(self, resultado: dict):
        self._emitir_funcao_js(
            "preencherResultado",
            _serializar_para_front(resultado or {}),
        )

    def _emitir_progresso(
        self,
        etapa: str,
        status: str,
        mensagem: str | None = None,
        percentual: int | float | None = None,
    ):
        mensagem_texto = "" if mensagem is None else str(mensagem)

        if mensagem_texto.startswith("PDF_NAME_READY::"):
            self._trazer_interface_para_frente(maximizar=True)

        self._last_progress = {
            "etapa": str(etapa or ""),
            "status": str(status or ""),
            "mensagem": mensagem_texto,
            "percentual": percentual,
        }

        self._emitir_funcao_js(
            "atualizarProgresso",
            self._last_progress,
        )

    def _trazer_interface_para_frente(self, maximizar=False):
        window = self._get_window()

        if window is None:
            return

        for method_name in ("restore", "show"):
            method = getattr(window, method_name, None)

            if not callable(method):
                continue

            try:
                method()
            except Exception:
                continue

        if maximizar:
            method = getattr(window, "maximize", None)

            if callable(method):
                try:
                    method()
                except Exception:
                    pass

        try:
            window.on_top = True
            time.sleep(0.12)
            window.on_top = False
        except Exception:
            pass

        self._evaluate_js_safe("try { window.focus(); } catch (e) {}")

    def _aguardar_aviso_operacional(self, tipo, payload=None, timeout=900):
        """
        Emite aviso operacional para o frontend.

        Avisos comuns seguem informativos. Avisos com confirmacao criam um
        Event e retornam ao backend para liberar a proxima etapa apos o clique.
        """

        payload = dict(payload or {})
        payload["tipo"] = str(tipo or "")
        evento = None
        tipo_aviso = str(tipo or "")

        if tipo_aviso in {"pdf_agua_esgoto", "pdf_agua_esgoto_agua_auto"}:
            self._trazer_interface_para_frente(maximizar=True)

        confirmacao_local = bool(payload.get("confirmacao_local"))

        if payload.get("aguardar_confirmacao") or payload.get("exigir_confirmacao"):
            notice_id = f"{time.time_ns()}-{threading.get_ident()}"
            evento = _ConfirmacaoOperacional()
            payload["notice_id"] = notice_id

            with self._notice_lock:
                self._notice_events[notice_id] = evento

            if confirmacao_local:
                threading.Thread(
                    target=self._monitorar_confirmacao_local,
                    args=(notice_id, evento),
                    daemon=True,
                    name=f"AvisoOperacional-{notice_id}",
                ).start()

            if tipo_aviso == "pdf":
                self._trazer_interface_para_frente(maximizar=True)

        emitido = self._emitir_funcao_js(
            "mostrarAvisoOperacional",
            tipo_aviso,
            _serializar_para_front(payload),
        )

        if evento is not None and not emitido:
            evento.concluir("interface_indisponivel")

        return evento or True

    # Encaminha logs publicos do backend para o balao de logs em tempo real.
    def _instalar_logger_tempo_real(self):
        logger = self._logger
        add_original = logger.add
        api = self

        def add_interceptado(etapa, msg, nivel="INFO", publico=False):
            add_original(etapa, msg, nivel=nivel, publico=publico)

            if publico:
                classe = "error" if str(nivel).upper() == "ERRO" else ""
                mensagem_publica = msg

                try:
                    if logger.public_logs:
                        mensagem_publica = logger.public_logs[-1]["msg"]
                except Exception:
                    mensagem_publica = msg

                api._emitir_log(mensagem_publica, classe)

        logger.add = add_interceptado
        return add_original

    def _restaurar_logger(self, add_original):
        self._logger.add = add_original

    def _executar_fluxo_com_callback(self, dados_tratados, resume_checkpoint=None):
        return self._controller.executar_fluxo(
            dados_tratados,
            progress_callback=self._emitir_progresso,
            notice_callback=self._aguardar_aviso_operacional,
            resume_checkpoint=resume_checkpoint,
            cancel_event=self._cancel_event,
        )

    def _executar_fluxo_multa_com_callback(self, dados_tratados, resume_checkpoint=None):
        return self._controller.executar_fluxo_multa_contratual(
            dados_tratados,
            progress_callback=self._emitir_progresso,
            notice_callback=self._aguardar_aviso_operacional,
            resume_checkpoint=resume_checkpoint,
            cancel_event=self._cancel_event,
        )

    @_requer_autenticacao
    def confirmar_aviso_operacional(self, notice_id):
        with self._notice_lock:
            evento = self._notice_events.pop(str(notice_id or ""), None)

        if evento is None:
            return {"ok": False, "msg": "Aviso operacional não encontrado."}

        evento.concluir("confirmado")
        return {"ok": True}

    # Entrada principal chamada pelo botao Gerar Boleto.
    @_requer_autenticacao
    def gerar_boleto(self, dados):
        resultado_final = {}
        concluido = threading.Event()

        with self._flow_lock:
            if self._flow_running:
                return {
                    "ok": False,
                    "msg": "Já existe uma criação de boleto em andamento.",
                    "tempo_real": True,
                }

            self._flow_running = True
            self._cancel_event.clear()
            self._last_progress = {}

        def worker():
            add_original = self._instalar_logger_tempo_real()

            try:
                dados_tratados = dict(dados)
                resume_checkpoint = dados_tratados.get("_resume_checkpoint")
                resume_mode = isinstance(resume_checkpoint, dict) and bool(
                    resume_checkpoint
                )

                if not resume_mode:
                    self._logger.clear()
                    self._emitir_reset_progresso()
                    self._emitir_status("Processando", "running")
                else:
                    self._emitir_status("Retomando", "running")

                dados_tratados["doc"] = limpar_doc(dados.get("doc", ""))[:14]
                dados_tratados["doc_info"] = analisar_doc(dados_tratados["doc"])
                dados_tratados["modo_valor"] = dados.get("modo_valor", "auto")

                erros = validar_dados(dados_tratados)

                if erros:
                    resultado_final.update(
                        _serializar_para_front(
                            {
                                "ok": False,
                                "msg": "Erros: " + ", ".join(erros),
                                "doc_info": dados_tratados["doc_info"],
                                "tempo_real": True,
                            }
                        )
                    )
                    self._emitir_status("Atenção", "error")
                    return

                dados_tratados["valor_info"] = analisar_valor(dados.get("valor", ""))
                dados_tratados["valor"] = dados_tratados["valor_info"]["formatado"]

                self._logger.add(
                    -1,
                    f"Documento preparado para SAP: {dados_tratados['doc']}",
                    publico=False,
                )
                self._logger.add(
                    -1,
                    f"Valor confirmado: {dados_tratados['valor_info']['formatado']}",
                    publico=False,
                )

                with self._sap_activity_lock:
                    resultado = self._executar_fluxo_com_callback(
                        dados_tratados,
                        resume_checkpoint=resume_checkpoint,
                    )
                resultado = _serializar_para_front(resultado)

                if not resultado["ok"]:
                    cancelado = bool(resultado.get("cancelado"))
                    self._logger.add(
                        -1,
                        (
                            f"Cancelamento na etapa {resultado['etapa']}: {resultado['mensagem']}"
                            if cancelado
                            else (
                                f"Resumo da falha: etapa {resultado['etapa']} - "
                                f"{resultado['mensagem']}. Veja o diagnóstico detalhado acima."
                            )
                        ),
                        nivel="ERRO",
                        publico=cancelado,
                    )

                    if resultado.get("dados"):
                        self._emitir_preencher_resultado(resultado["dados"] or {})

                    resultado_final.update(
                        _serializar_para_front(
                            {
                                "ok": False,
                                "cancelado": cancelado,
                                "msg": resultado["mensagem"],
                                "logs": self._logger.get_logs(public_only=True),
                                "doc_info": dados_tratados["doc_info"],
                                "resultado": resultado.get("dados") or {},
                                "acao_pendente": resultado.get("acao_pendente"),
                                "checkpoint": resultado.get("checkpoint"),
                                "tempo_real": True,
                            }
                        )
                    )

                    self._emitir_status(
                        "Cancelado" if cancelado else "Falha no processamento",
                        "error",
                    )
                    return

                dados_resultado = resultado.get("dados") or {}
                resultado["dados"] = dados_resultado

                registro_historico = registrar_historico_pedido(
                    dados_tratados,
                    dados_resultado,
                    logger=self._logger,
                )

                if registro_historico:
                    dados_resultado["historico_id"] = registro_historico.get("id")
                    dados_resultado["historico_txt"] = registro_historico.get("txt_path")

                payload_sucesso = {
                    "ok": True,
                    "logs": self._logger.get_logs(public_only=True),
                    "resultado": dados_resultado,
                    "doc_info": dados_tratados["doc_info"],
                    "valor_info": dados_tratados["valor_info"],
                    "checkpoint": None,
                    "tempo_real": True,
                }

                resultado_final.update(_serializar_para_front(payload_sucesso))
                self._emitir_preencher_resultado(resultado["dados"] or {})
                self._emitir_status("Concluído", "success")

            except Exception as e:
                erro_publico = (
                    "Falha detalhada fora de uma etapa SAP.\n"
                    "O que o sistema fazia: preparar, executar ou finalizar a chamada entre interface e backend.\n"
                    f"Bloqueio técnico retornado: {str(e) or 'sem detalhe retornado'}.\n"
                    f"Log técnico completo: {self._logger.log_file_path}."
                )
                self._logger.add(
                    -1,
                    erro_publico,
                    nivel="ERRO",
                    publico=True,
                )
                self._logger.add(
                    -1,
                    traceback.format_exc(),
                    nivel="DEBUG",
                    publico=False,
                )

                resultado_final.update(
                    {
                        "ok": False,
                        "msg": "Erro inesperado no processamento.",
                        "logs": _serializar_para_front(
                            self._logger.get_logs(public_only=True)
                        ),
                        "tempo_real": True,
                    }
                )
                self._emitir_status("Falha no processamento", "error")

            finally:
                self._restaurar_logger(add_original)
                with self._flow_lock:
                    self._flow_running = False
                concluido.set()

        threading.Thread(target=worker, daemon=True).start()
        concluido.wait()

        return _serializar_para_front(resultado_final)

    def _executar_acao_cliente_sap(self, dados, executor, status_texto):
        resultado_final = {}
        concluido = threading.Event()

        with self._flow_lock:
            if self._flow_running:
                return {
                    "ok": False,
                    "msg": "Ja existe uma acao SAP em andamento.",
                    "tempo_real": True,
                }

            self._flow_running = True
            self._cancel_event.clear()

        def worker():
            add_original = self._instalar_logger_tempo_real()

            try:
                self._emitir_status(status_texto, "running")
                self._logger.add(-1, status_texto, publico=True)
                with self._sap_activity_lock:
                    session = conectar_sap()
                    resultado = executor(
                        session,
                        dados,
                        self._logger,
                        progress_callback=self._emitir_progresso,
                    )
                resultado = _serializar_para_front(resultado)

                payload = {
                    "ok": bool(resultado.get("ok")),
                    "msg": resultado.get("mensagem") or "",
                    "resultado": resultado.get("dados") or {},
                    "logs": self._logger.get_logs(public_only=True),
                    "tempo_real": True,
                }

                resultado_final.update(_serializar_para_front(payload))

                if payload["ok"]:
                    self._emitir_status("Concluido", "success")
                else:
                    self._emitir_status("Falha no processamento", "error")
            except Exception as exc:
                self._logger.add(
                    -1,
                    f"Falha na acao de cadastro SAP: {exc}",
                    nivel="ERRO",
                    publico=True,
                )
                self._logger.add(
                    -1,
                    traceback.format_exc(),
                    nivel="DEBUG",
                    publico=False,
                )
                resultado_final.update(
                    {
                        "ok": False,
                        "msg": "Erro inesperado na acao de cadastro SAP.",
                        "logs": _serializar_para_front(
                            self._logger.get_logs(public_only=True)
                        ),
                        "tempo_real": True,
                    }
                )
                self._emitir_status("Falha no processamento", "error")
            finally:
                self._restaurar_logger(add_original)
                with self._flow_lock:
                    self._flow_running = False
                concluido.set()

        threading.Thread(target=worker, daemon=True).start()
        concluido.wait()

        return _serializar_para_front(resultado_final)

    @_requer_autenticacao
    def gerar_boleto_multa_contratual(self, dados):
        resultado_final = {}
        concluido = threading.Event()

        with self._flow_lock:
            if self._flow_running:
                return {
                    "ok": False,
                    "msg": "JÃ¡ existe uma criaÃ§Ã£o de boleto em andamento.",
                    "tempo_real": True,
                }

            self._flow_running = True
            self._cancel_event.clear()
            self._last_progress = {}

        def worker():
            add_original = self._instalar_logger_tempo_real()

            try:
                dados_tratados = dict(dados)
                resume_checkpoint = dados_tratados.get("_resume_checkpoint")
                resume_mode = isinstance(resume_checkpoint, dict) and bool(
                    resume_checkpoint
                )

                if not resume_mode:
                    self._logger.clear()
                    self._emitir_reset_progresso()
                    self._emitir_status("Processando Multa Contratual", "running")
                else:
                    self._emitir_status("Retomando Multa Contratual", "running")

                contrato = "".join(
                    ch for ch in str(dados.get("contrato", "")) if ch.isdigit()
                )[:9]
                dados_tratados["doc"] = limpar_doc(dados.get("doc", ""))[:14]
                dados_tratados["doc_info"] = analisar_doc(dados_tratados["doc"])
                dados_tratados["tipo"] = "multa_contratual"
                dados_tratados["modalidade"] = "multa_contratual"
                dados_tratados["contrato"] = contrato

                erros = validar_dados_multa_contratual(dados_tratados)

                if erros:
                    resultado_final.update(
                        _serializar_para_front(
                            {
                                "ok": False,
                                "msg": "Erros: " + ", ".join(erros),
                                "doc_info": dados_tratados["doc_info"],
                                "tempo_real": True,
                            }
                        )
                    )
                    self._emitir_status("AtenÃ§Ã£o", "error")
                    return

                dados_tratados["valor_info"] = analisar_valor_sem_limite(
                    dados.get("valor", "")
                )
                dados_tratados["valor"] = dados_tratados["valor_info"]["formatado"]

                self._logger.add(
                    -1,
                    "Fluxo Multa Contratual preparado para SAP.",
                    publico=True,
                )
                self._logger.add(
                    -1,
                    f"Documento preparado para SAP: {dados_tratados['doc']}",
                    publico=False,
                )
                self._logger.add(
                    -1,
                    f"Contrato informado: {dados_tratados['contrato']}",
                    publico=False,
                )
                self._logger.add(
                    -1,
                    f"Valor confirmado: {dados_tratados['valor_info']['formatado']}",
                    publico=False,
                )

                with self._sap_activity_lock:
                    resultado = self._executar_fluxo_multa_com_callback(
                        dados_tratados,
                        resume_checkpoint=resume_checkpoint,
                    )
                resultado = _serializar_para_front(resultado)

                if not resultado["ok"]:
                    cancelado = bool(resultado.get("cancelado"))
                    self._logger.add(
                        -1,
                        (
                            f"Cancelamento na etapa {resultado['etapa']}: {resultado['mensagem']}"
                            if cancelado
                            else (
                                f"Resumo da falha: etapa {resultado['etapa']} - "
                                f"{resultado['mensagem']}. Veja o diagnÃ³stico detalhado acima."
                            )
                        ),
                        nivel="ERRO",
                        publico=cancelado,
                    )

                    if resultado.get("dados"):
                        self._emitir_preencher_resultado(resultado["dados"] or {})

                    resultado_final.update(
                        _serializar_para_front(
                            {
                                "ok": False,
                                "cancelado": cancelado,
                                "msg": resultado["mensagem"],
                                "logs": self._logger.get_logs(public_only=True),
                                "doc_info": dados_tratados["doc_info"],
                                "resultado": resultado.get("dados") or {},
                                "acao_pendente": resultado.get("acao_pendente"),
                                "checkpoint": resultado.get("checkpoint"),
                                "tempo_real": True,
                            }
                        )
                    )

                    self._emitir_status(
                        "Cancelado" if cancelado else "Falha no processamento",
                        "error",
                    )
                    return

                dados_resultado = resultado.get("dados") or {}
                resultado["dados"] = dados_resultado

                registro_historico = registrar_historico_pedido(
                    dados_tratados,
                    dados_resultado,
                    logger=self._logger,
                )

                if registro_historico:
                    dados_resultado["historico_id"] = registro_historico.get("id")
                    dados_resultado["historico_txt"] = registro_historico.get("txt_path")

                payload_sucesso = {
                    "ok": True,
                    "logs": self._logger.get_logs(public_only=True),
                    "resultado": dados_resultado,
                    "doc_info": dados_tratados["doc_info"],
                    "valor_info": dados_tratados["valor_info"],
                    "checkpoint": None,
                    "tempo_real": True,
                }

                resultado_final.update(_serializar_para_front(payload_sucesso))
                self._emitir_preencher_resultado(resultado["dados"] or {})
                if dados_resultado.get("finalizacao_pdf_manual_pendente"):
                    self._emitir_status("Finalize os dois boletos", "running")
                else:
                    self._emitir_status("ConcluÃ­do", "success")

            except Exception as e:
                erro_publico = (
                    "Falha detalhada fora de uma etapa SAP.\n"
                    "O que o sistema fazia: preparar, executar ou finalizar a chamada de Multa Contratual.\n"
                    f"Bloqueio tÃ©cnico retornado: {str(e) or 'sem detalhe retornado'}.\n"
                    f"Log tÃ©cnico completo: {self._logger.log_file_path}."
                )
                self._logger.add(
                    -1,
                    erro_publico,
                    nivel="ERRO",
                    publico=True,
                )
                self._logger.add(
                    -1,
                    traceback.format_exc(),
                    nivel="ERRO",
                    publico=False,
                )
                resultado_final.update(
                    {
                        "ok": False,
                        "msg": str(e),
                        "logs": self._logger.get_logs(public_only=True),
                        "tempo_real": True,
                    }
                )
                self._emitir_status("Falha no processamento", "error")
            finally:
                self._restaurar_logger(add_original)
                with self._flow_lock:
                    self._flow_running = False
                concluido.set()

        threading.Thread(target=worker, daemon=True).start()
        concluido.wait()

        return _serializar_para_front(resultado_final)

    @_requer_autenticacao
    def cadastrar_cliente_sap(self, dados):
        return self._executar_acao_cliente_sap(
            dados,
            criar_cliente,
            "Cadastrando cliente no SAP",
        )

    @_requer_autenticacao
    def cadastrar_cliente_multa_contratual_sap(self, dados):
        payload = dict(dados or {})
        payload["tipo"] = "multa_contratual"
        return self._executar_acao_cliente_sap(
            payload,
            criar_cliente_multa_contratual,
            "Cadastrando cliente de Multa Contratual no SAP",
        )

    @_requer_autenticacao
    def adicionar_setores_cliente_sap(self, dados):
        return self._executar_acao_cliente_sap(
            dados,
            adicionar_setores_cliente,
            "Cadastrando setores no SAP",
        )

    @_requer_autenticacao
    def listar_modelos_saldo_contas(self):
        try:
            return {
                "ok": True,
                "modelos": _serializar_para_front(listar_modelos_saldo_contas_salvos()),
            }
        except Exception as exc:
            return {"ok": False, "msg": f"Nao foi possivel listar os modelos: {exc}"}

    @_requer_autenticacao
    def analisar_planilha_saldo_contas(self, caminho, modelo_id=""):
        try:
            modelo = obter_modelo_saldo_contas(modelo_id) if modelo_id else None
            if modelo_id and modelo is None:
                raise ValueError("O modelo selecionado nao foi encontrado.")
            if modelo is None:
                deteccao = detectar_modelo_arquivo_saldo_contas(caminho)
                resultado = analisar_arquivo_saldo_contas(
                    caminho,
                    aba_modelo=deteccao["aba_modelo"],
                    modelo=deteccao["modelo"],
                )
                resultado["requer_confirmacao"] = True
                resultado["avisos_deteccao"] = deteccao.get("avisos") or []
                resultado["msg"] = deteccao["msg"]
            else:
                resultado = analisar_arquivo_saldo_contas(
                    caminho,
                    aba_modelo=modelo["aba"],
                    modelo=modelo,
                )
                resultado["requer_confirmacao"] = False
            return _serializar_para_front(resultado)
        except Exception as exc:
            return {
                "ok": False,
                "msg": f"Nao foi possivel analisar a planilha: {exc}",
            }

    @_requer_autenticacao
    def detectar_modelo_saldo_contas(self, caminho, aba_modelo=""):
        try:
            deteccao = detectar_modelo_arquivo_saldo_contas(
                caminho,
                aba_modelo=aba_modelo or None,
            )
            analise = analisar_arquivo_saldo_contas(
                caminho,
                aba_modelo=deteccao["aba_modelo"],
                modelo=deteccao["modelo"],
            )
            analise["requer_confirmacao"] = True
            analise["avisos_deteccao"] = deteccao.get("avisos") or []
            analise["msg"] = deteccao["msg"]
            return _serializar_para_front(analise)
        except Exception as exc:
            return {"ok": False, "msg": f"Nao foi possivel detectar as contas: {exc}"}

    @_requer_autenticacao
    def salvar_modelo_saldo_contas(self, dados):
        try:
            payload = dict(dados or {})
            caminho = str(payload.get("caminho") or "").strip()
            modelo = payload.get("modelo")
            analise = analisar_arquivo_saldo_contas(
                caminho,
                modelo=modelo,
            )
            if not analise.get("modelo_valido"):
                diferencas = [
                    str(item).strip()
                    for item in (analise.get("erros_modelo") or [])
                    if str(item).strip()
                ]
                mensagem = "Corrija as diferencas do modelo antes de salva-lo."
                if diferencas:
                    mensagem = (
                        "Nao foi possivel salvar porque o modelo nao corresponde a planilha:\n- "
                        + "\n- ".join(diferencas[:8])
                    )
                    if len(diferencas) > 8:
                        mensagem += f"\n- E mais {len(diferencas) - 8} diferenca(s)."
                return {
                    "ok": False,
                    "msg": mensagem,
                    "diferencas": diferencas,
                    "analise": _serializar_para_front(analise),
                }
            salvo = salvar_modelo_saldo_contas_local(modelo)
            analise = analisar_arquivo_saldo_contas(
                caminho,
                aba_modelo=salvo["aba"],
                modelo=salvo,
            )
            analise["requer_confirmacao"] = False
            return {
                "ok": True,
                "msg": "Modelo salvo e confirmado com sucesso.",
                "modelo": _serializar_para_front(salvo),
                "analise": _serializar_para_front(analise),
                "modelos": _serializar_para_front(listar_modelos_saldo_contas_salvos()),
            }
        except Exception as exc:
            return {"ok": False, "msg": f"Nao foi possivel salvar o modelo: {exc}"}

    @_requer_autenticacao
    def excluir_modelo_saldo_contas(self, modelo_id):
        try:
            removido = excluir_modelo_saldo_contas_salvo(modelo_id)
            return {
                "ok": removido,
                "msg": "Modelo excluido." if removido else "Modelo nao encontrado.",
                "modelos": _serializar_para_front(listar_modelos_saldo_contas_salvos()),
            }
        except Exception as exc:
            return {"ok": False, "msg": f"Nao foi possivel excluir o modelo: {exc}"}

    @_requer_autenticacao
    def selecionar_planilha_saldo_contas(self, pasta_inicial=""):
        window = self._get_window()

        if window is None:
            return {
                "ok": False,
                "msg": "A janela do aplicativo nao esta disponivel para selecionar a planilha.",
            }

        pasta_inicial = str(pasta_inicial or "").strip()
        caminho_inicial = Path(pasta_inicial).expanduser() if pasta_inicial else None

        if caminho_inicial and caminho_inicial.is_file():
            pasta_inicial = str(caminho_inicial.parent)

        try:
            selecionadas = window.create_file_dialog(
                webview.FileDialog.OPEN,
                directory=pasta_inicial,
                allow_multiple=False,
                file_types=("Planilha Excel (*.xlsx)",),
            )
        except Exception as exc:
            return {
                "ok": False,
                "msg": f"Nao foi possivel abrir a selecao de planilha: {exc}",
            }

        if not selecionadas:
            return {
                "ok": False,
                "cancelado": True,
                "msg": "Selecao de planilha cancelada.",
            }

        selecionada = (
            selecionadas[0]
            if isinstance(selecionadas, (list, tuple))
            else selecionadas
        )

        return self.analisar_planilha_saldo_contas(selecionada)

    @_requer_autenticacao
    def executar_saldo_contas_sap(self, dados):
        resultado_final = {}
        concluido = threading.Event()

        with self._flow_lock:
            if self._flow_running:
                return {
                    "ok": False,
                    "msg": "Ja existe uma acao SAP em andamento.",
                    "tempo_real": True,
                }

            self._flow_running = True

        def worker():
            add_original = self._instalar_logger_tempo_real()
            pythoncom = None
            inicio_total = time.monotonic()

            try:
                try:
                    import pythoncom as pythoncom_module

                    pythoncom_module.CoInitialize()
                    pythoncom = pythoncom_module
                except Exception:
                    pythoncom = None

                payload = dict(dados or {})
                caminho = str(payload.get("caminho") or "").strip()
                mes = payload.get("mes")
                ano = payload.get("ano")
                modelo_id = str(payload.get("modelo_id") or "").strip()
                modelo = obter_modelo_saldo_contas(modelo_id) if modelo_id else None
                if modelo_id and modelo is None:
                    raise ValueError("O modelo de contas selecionado nao foi encontrado.")
                aba_modelo = modelo["aba"] if modelo is not None else ABA_MODELO_SALDO_CONTAS
                analise = analisar_arquivo_saldo_contas(
                    caminho,
                    aba_modelo=aba_modelo,
                    modelo=modelo,
                )

                if not analise.get("ok") or not analise.get("modelo_valido"):
                    raise ValueError(
                        "A planilha precisa passar pela confirmacao do modelo antes da consulta."
                    )

                plano = planejar_consultas_saldo_contas(
                    analise,
                    payload.get("grupos_selecionados"),
                )
                etapas = plano["lotes"]
                destinos = plano["destinos"]
                contas_individuais = sum(etapa["individual"] for etapa in etapas)
                self._logger.clear()
                self._emitir_status("Consultando saldos no SAP", "running")
                self._emitir_progresso(
                    "PLANILHA", "done",
                    f"{len(destinos)} contas de {len(plano['grupos'])} grupo(s) validadas.", 100,
                )
                self._logger.add(
                    -1,
                    f"Consultando {len(destinos)} contas em {len(etapas)} consultas SAP "
                    f"({contas_individuais} individuais destacadas em amarelo).",
                    publico=True,
                )
                saldos = {}
                tempos_consultas = []
                with self._sap_activity_lock:
                    session = conectar_sap()
                    for indice, etapa in enumerate(etapas, start=1):
                        descricao = (
                            etapa["contas"][0] if etapa["individual"]
                            else f"lote com {len(etapa['contas'])} contas"
                        )
                        self._emitir_progresso(
                            "SAP_SALDOS", "running",
                            f"Consulta {indice}/{len(etapas)}: {etapa['grupo']} - {descricao}",
                            int(100 * (indice - 1) / len(etapas)),
                        )
                        self._logger.add(
                            -1, f"{indice}/{len(etapas)} - {etapa['grupo']}: {descricao}",
                            publico=True,
                        )
                        # O fluxo continua serial na MESMA sessao SAP.
                        # Nao tenta gravar a planilha antes de finalizar todas.
                        inicio_consulta = time.monotonic()
                        consulta_parcial = executar_consulta_saldos_sap(
                            session, etapa["contas"], mes, ano,
                            logger=self._logger,
                            # Evita que o progresso interno reinicie o progresso global.
                            progress_callback=None,
                            limpar_contas_anteriores=indice > 1,
                        )
                        duracao_consulta = round(time.monotonic() - inicio_consulta, 2)
                        tempos_consultas.append(
                            {
                                "ordem": indice,
                                "grupo": etapa["grupo"],
                                "descricao": descricao,
                                "duracao_segundos": duracao_consulta,
                            }
                        )
                        for conta, valor in consulta_parcial["saldos"].items():
                            if conta in saldos:
                                raise RuntimeError(f"Conta {conta} duplicada entre consultas.")
                            saldos[conta] = valor
                        self._emitir_progresso(
                            "SAP_SALDOS", "running",
                            f"{len(saldos)}/{len(destinos)} saldos confirmados.",
                            int(100 * indice / len(etapas)),
                        )
                        self._logger.add(
                            -1,
                            f"Consulta {indice}/{len(etapas)} concluida em {duracao_consulta:.1f} s.",
                            publico=True,
                        )

                faltantes = sorted(destinos.keys() - saldos.keys())
                if faltantes:
                    raise RuntimeError(
                        "Consulta incompleta, nenhuma planilha foi alterada. "
                        f"Contas faltantes: {', '.join(faltantes)}"
                    )
                self._emitir_progresso(
                    "CONFERIR_SALDOS", "running",
                    f"Conferindo {len(saldos)} subtotais, inclusive valores zero.", 35,
                )
                total = sum(saldos.values(), start=0)
                total_formatado = (
                    f"{float(total):,.2f}".replace(",", "X")
                    .replace(".", ",").replace("X", ".")
                )
                self._emitir_progresso(
                    "CONFERIR_SALDOS", "done",
                    f"Total das contas selecionadas: R$ {total_formatado}.", 100,
                )
                self._emitir_progresso(
                    "SALVAR_RELATORIO", "running",
                    "Criando copia preenchida (sem alterar grupos nao selecionados).", 40,
                )
                planilha = preencher_planilha_saldo_contas(
                    caminho, saldos, mes, ano,
                    aba_modelo=aba_modelo,
                    grupos_selecionados=plano["grupos"],
                    modelo=modelo,
                )
                consulta = {
                    "contas_consultadas": len(destinos),
                    "contas_retornadas": len(saldos),
                    "consultas_executadas": len(etapas),
                    "contas_individuais": contas_individuais,
                    "grupos_selecionados": plano["grupos"],
                    "data_inicial": "",
                    "data_final": consulta_parcial["data_final"],
                    "tempos_consultas": tempos_consultas,
                }
                analise_saida = analisar_arquivo_saldo_contas(
                    planilha["caminho_saida"],
                    aba_modelo=aba_modelo,
                    modelo=modelo,
                )
                self._emitir_progresso(
                    "SALVAR_RELATORIO",
                    "done",
                    f"Relatorio salvo: {planilha['arquivo_saida']}",
                    100,
                )
                self._logger.add(
                    -1,
                    f"Relatorio salvo em {planilha['caminho_saida']}",
                    publico=True,
                )
                grupos_selecionados = set(plano["grupos"])
                contas_resumo = []
                grupos_resumo = []
                for grupo in analise.get("grupos") or []:
                    if grupo.get("id") not in grupos_selecionados:
                        continue
                    total_grupo = 0
                    quantidade_grupo = 0
                    for item in grupo.get("contas") or []:
                        conta = item.get("conta")
                        if conta not in saldos:
                            continue
                        valor = saldos[conta]
                        total_grupo += valor
                        quantidade_grupo += 1
                        contas_resumo.append(
                            {
                                "conta": conta,
                                "grupo": grupo.get("nome"),
                                "celula": item.get("celula"),
                                "modo_consulta": "individual" if item.get("individual") else "lote",
                                "valor": float(valor),
                            }
                        )
                    grupos_resumo.append(
                        {
                            "id": grupo.get("id"),
                            "nome": grupo.get("nome"),
                            "quantidade": quantidade_grupo,
                            "total": float(total_grupo),
                        }
                    )

                duracao_total = round(time.monotonic() - inicio_total, 2)
                dados_historico = {
                    "mes": mes,
                    "ano": ano,
                    "data_final": consulta["data_final"],
                    "arquivo_origem": analise.get("arquivo"),
                    "arquivo_saida": planilha["arquivo_saida"],
                    "caminho_saida": planilha["caminho_saida"],
                    "grupos": grupos_resumo,
                    "contas": contas_resumo,
                    "consultas_executadas": len(etapas),
                    "contas_individuais": contas_individuais,
                    "duracao_segundos": duracao_total,
                    "tempos_consultas": tempos_consultas,
                    "total": float(total),
                    "modelo_id": modelo_id,
                    "modelo_nome": modelo.get("nome") if modelo else "Modelo SET.26",
                }
                registro_historico = registrar_historico_saldo_contas(
                    dados_historico,
                    logger=self._logger,
                )
                self._emitir_status("Relatorio de saldos concluido", "success")

                resultado_final.update(
                    _serializar_para_front(
                        {
                            "ok": True,
                            "msg": "Consulta concluida e planilha preenchida.",
                            "caminho_saida": planilha["caminho_saida"],
                            "arquivo_saida": planilha["arquivo_saida"],
                            "analise": analise_saida,
                            "contas_preenchidas": contas_resumo,
                            "grupos_preenchidos": grupos_resumo,
                            "historico_id": (
                                registro_historico.get("id")
                                if registro_historico else None
                            ),
                            "resumo": {
                                **consulta,
                                "saldos": None,
                                "total": planilha["total"],
                                "duracao_segundos": duracao_total,
                            },
                            "logs": self._logger.get_logs(public_only=True),
                            "tempo_real": True,
                        }
                    )
                )
            except Exception as exc:
                mensagem = str(exc) or "Falha inesperada na consulta de saldos."
                self._logger.add(
                    -1,
                    f"Falha na consulta de saldos: {mensagem}",
                    nivel="ERRO",
                    publico=True,
                )
                self._logger.add(
                    -1,
                    traceback.format_exc(),
                    nivel="DEBUG",
                    publico=False,
                )
                resultado_final.update(
                    {
                        "ok": False,
                        "msg": mensagem,
                        "logs": _serializar_para_front(
                            self._logger.get_logs(public_only=True)
                        ),
                        "tempo_real": True,
                    }
                )
                self._emitir_status("Falha na consulta de saldos", "error")
            finally:
                if pythoncom is not None:
                    try:
                        pythoncom.CoUninitialize()
                    except Exception:
                        pass

                self._restaurar_logger(add_original)

                with self._flow_lock:
                    self._flow_running = False

                concluido.set()

        threading.Thread(
            target=worker,
            daemon=True,
            name="SaldoContasSAP",
        ).start()
        concluido.wait()
        return _serializar_para_front(resultado_final)

    def _arquivo_preferencias_extratos(self) -> Path:
        return get_runtime_root() / _EXTRATOS_PREFERENCIAS_ARQUIVO

    @_requer_autenticacao
    def obter_pasta_padrao_extratos(self):
        arquivo = self._arquivo_preferencias_extratos()

        if not arquivo.exists():
            return {"ok": True, "pasta_base": ""}

        try:
            payload = json.loads(arquivo.read_text(encoding="utf-8-sig"))
        except Exception:
            return {"ok": True, "pasta_base": ""}

        pasta_base = _normalizar_pasta_base_extratos(
            (payload or {}).get("pasta_base")
        )

        if (
            not pasta_base
            or not _pasta_extratos_em_rede_corporativa(pasta_base)
            or not pasta_base.is_dir()
        ):
            return {
                "ok": True,
                "pasta_base": "",
                "configuracao_invalida": True,
                "msg": (
                    "A pasta corporativa salva nao esta disponivel. "
                    "Reconecte a unidade de rede e selecione a pasta EXTRATOS."
                ),
            }

        return {"ok": True, "pasta_base": str(pasta_base)}

    @_requer_autenticacao
    def selecionar_pasta_base_extratos(self, pasta_inicial="", definir_padrao=True):
        window = self._get_window()

        if window is None:
            return {
                "ok": False,
                "msg": "A janela do aplicativo nao esta disponivel para selecionar a pasta.",
            }

        pasta_inicial = str(pasta_inicial or "").strip()

        try:
            selecionadas = window.create_file_dialog(
                webview.FileDialog.FOLDER,
                directory=pasta_inicial,
                allow_multiple=False,
            )
        except Exception as exc:
            return {
                "ok": False,
                "msg": f"Nao foi possivel abrir a selecao de pasta: {exc}",
            }

        if not selecionadas:
            return {"ok": False, "cancelado": True, "msg": "Selecao de pasta cancelada."}

        selecionada = (
            selecionadas[0]
            if isinstance(selecionadas, (list, tuple))
            else selecionadas
        )
        pasta_base = _normalizar_pasta_base_extratos(selecionada)

        if not _pasta_extratos_em_rede_corporativa(pasta_base):
            return {
                "ok": False,
                "msg": (
                    "Selecione a pasta EXTRATOS em uma unidade de rede corporativa. "
                    "Pastas locais e Area de Trabalho nao sao aceitas."
                ),
            }

        if not pasta_base.exists() or not pasta_base.is_dir():
            return {
                "ok": False,
                "msg": (
                    "A pasta corporativa selecionada nao esta disponivel. "
                    "Verifique a conexao com a unidade de rede."
                ),
            }

        if definir_padrao:
            arquivo = self._arquivo_preferencias_extratos()
            temporario = arquivo.with_suffix(".tmp")
            payload = {
                "pasta_base": str(pasta_base),
                "atualizado_em": time.strftime("%Y-%m-%d %H:%M:%S"),
            }

            try:
                arquivo.parent.mkdir(parents=True, exist_ok=True)
                temporario.write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                temporario.replace(arquivo)
            except Exception as exc:
                return {
                    "ok": False,
                    "msg": f"A pasta foi selecionada, mas nao foi possivel salva-la como padrao: {exc}",
                }

        return {
            "ok": True,
            "pasta_base": str(pasta_base),
            "salva_como_padrao": bool(definir_padrao),
        }

    @_requer_autenticacao
    def abrir_navegador_caixa(self, navegador="opera"):
        return _serializar_para_front(abrir_navegador_caixa(navegador))

    @_requer_autenticacao
    def diagnosticar_chrome_caixa(self, navegador="opera"):
        return _serializar_para_front(diagnosticar_chrome_caixa(navegador))

    @_requer_autenticacao
    def baixar_extratos_caixa(self, dados):
        resultado_final = {}
        concluido = threading.Event()

        with self._flow_lock:
            if self._flow_running:
                return {
                    "ok": False,
                    "msg": "Ja existe uma acao em andamento.",
                    "tempo_real": True,
                }

            self._flow_running = True
            self._extrato_running = True
            self._cancel_event.clear()
            self._extrato_pause_event.clear()
            self._last_progress = {}

        def worker():
            add_original = self._instalar_logger_tempo_real()

            try:
                self._logger.clear()
                self._emitir_reset_progresso()
                self._emitir_status("Baixando extrato", "running")
                self._logger.add(-1, "Iniciando rotina Caixa GovConta.", publico=True)

                resultado = executar_download_extrato_atual(
                    dict(dados or {}),
                    log_callback=self._emitir_log,
                    progress_callback=self._emitir_progresso,
                    cancel_event=self._cancel_event,
                    pause_event=self._extrato_pause_event,
                )

                resultado = _serializar_para_front(resultado)
                resultado["logs"] = self._logger.get_logs(public_only=True)
                resultado["tempo_real"] = True
                resultado_final.update(resultado)

                if resultado.get("pausado"):
                    self._emitir_status("Extratos pausados", "idle")
                elif resultado.get("ok"):
                    self._emitir_status("Extrato baixado", "success")
                else:
                    self._emitir_status("Falha nos extratos", "error")

            except Exception as exc:
                self._logger.add(
                    -1,
                    (
                        "Falha detalhada na rotina Caixa.\n"
                        "O que o sistema fazia: controlar o navegador logado e exportar o extrato em PDF.\n"
                        f"Bloqueio tecnico retornado: {str(exc) or 'sem detalhe retornado'}."
                    ),
                    nivel="ERRO",
                    publico=True,
                )
                self._logger.add(
                    -1,
                    traceback.format_exc(),
                    nivel="DEBUG",
                    publico=False,
                )
                resultado_final.update(
                    {
                        "ok": False,
                        "msg": "Erro inesperado na rotina Caixa.",
                        "logs": self._logger.get_logs(public_only=True),
                        "tempo_real": True,
                    }
                )
                self._emitir_status("Falha nos extratos", "error")
            finally:
                self._restaurar_logger(add_original)
                with self._flow_lock:
                    self._extrato_running = False
                    self._flow_running = False
                concluido.set()

        threading.Thread(target=worker, daemon=True).start()
        concluido.wait()

        return _serializar_para_front(resultado_final)

    @_requer_autenticacao
    def pausar_extratos_caixa(self):
        with self._flow_lock:
            if not self._flow_running or not self._extrato_running:
                return {
                    "ok": False,
                    "msg": "Nenhuma baixa de extratos em andamento.",
                }

            self._extrato_pause_event.set()

        self._emitir_status("Pausando extratos", "running")
        self._logger.add(
            -1,
            "Pausa solicitada. A conta atual sera finalizada antes da interrupcao.",
            publico=True,
        )
        return {
            "ok": True,
            "msg": "Pausa solicitada. A conta atual sera concluida antes de parar.",
        }

    # Solicita parada no proximo ponto seguro do fluxo SAP em execucao.
    @_requer_autenticacao
    def cancelar_fluxo(self):
        with self._flow_lock:
            if not self._flow_running:
                return {
                    "ok": False,
                    "msg": "Nenhuma criação de boleto em andamento.",
                }

            self._cancel_event.set()

        ultima_etapa = self._last_progress.get("etapa") or "etapa ainda não informada"
        ultima_msg = self._last_progress.get("mensagem") or "aguardando próximo ponto seguro"
        mensagem = (
            "Cancelamento solicitado. O processo será interrompido no próximo ponto seguro. "
            f"Última etapa informada: {ultima_etapa}. Status: {ultima_msg}."
        )

        self._logger.add(-1, mensagem, nivel="ERRO", publico=True)
        self._emitir_log(mensagem, "error")
        self._emitir_status("Cancelando", "error")

        return {
            "ok": True,
            "msg": mensagem,
            "ultima_etapa": ultima_etapa,
            "ultima_mensagem": ultima_msg,
        }

    # Recebe os dados do Fale Conosco e delega validação/envio ao backend.
    @_requer_autenticacao
    def enviar_atendimento(self, dados):
        try:
            resultado = process_support_request(dados)
        except ValueError as exc:
            return {"ok": False, "msg": str(exc)}
        except Exception:
            self._logger.add(
                -1,
                traceback.format_exc(),
                nivel="DEBUG",
                publico=False,
            )
            return {
                "ok": False,
                "msg": "Falha ao registrar o pedido de atendimento.",
            }

        return _serializar_para_front(
            {
                "ok": True,
                "msg": (
                    "Seu pedido foi enviado com sucesso para o atendimento interno. "
                    f"Protocolo: {resultado['protocolo']}."
                ),
                "protocolo": resultado["protocolo"],
                "snapshot_path": resultado["snapshot_path"],
                "email_destino": resultado.get("destination_email"),
            }
        )

    # Retorna informações técnicas usadas para diagnóstico local.
    @_requer_autenticacao
    def obter_diagnostico(self):
        from backend.flows.xd03 import cache_cliente

        return _serializar_para_front(
            {
                "ok": True,
                "app": SETTINGS.get("app", {}),
                "runtime": SETTINGS.get("_meta", {}),
                "cache": cache_cliente.stats(),
                "history": diagnostico_historico(),
                "drafts": diagnostico_rascunhos(),
                "log_file": self._logger.log_file_path,
            }
        )

    # Lista registros gravados no historico compartilhado/local.
    @_requer_autenticacao
    def listar_historico(self, filtro="", limite=None):
        try:
            return _serializar_para_front(
                {
                    "ok": True,
                    "itens": listar_historico_pedidos(filtro=filtro, limite=limite),
                }
            )
        except Exception as exc:
            return {
                "ok": False,
                "msg": f"Falha ao carregar histórico: {exc}",
                "itens": [],
            }

    # Retorna o detalhe completo de um registro de historico.
    @_requer_autenticacao
    def obter_historico(self, registro_id):
        try:
            registro = obter_historico_pedido(registro_id)
        except Exception as exc:
            return {
                "ok": False,
                "msg": f"Falha ao abrir histórico: {exc}",
            }

        if not registro:
            return {
                "ok": False,
                "msg": "Registro de histórico não encontrado.",
            }

        return _serializar_para_front(
            {
                "ok": True,
                "registro": registro,
            }
        )

    # Lista rascunhos de clientes salvos para completar depois.
    @_requer_autenticacao
    def listar_rascunhos_clientes(self, filtro="", limite=None):
        try:
            return _serializar_para_front(
                {
                    "ok": True,
                    "itens": listar_rascunhos_clientes(filtro=filtro, limite=limite),
                }
            )
        except Exception as exc:
            return {
                "ok": False,
                "msg": f"Falha ao carregar rascunhos: {exc}",
                "itens": [],
            }

    # Cria ou atualiza um rascunho de cliente.
    @_requer_autenticacao
    def salvar_rascunho_cliente(self, payload):
        try:
            item = salvar_rascunho_cliente(payload if isinstance(payload, dict) else {})
            return _serializar_para_front(
                {
                    "ok": True,
                    "item": item,
                    "itens": listar_rascunhos_clientes(),
                }
            )
        except Exception as exc:
            return {
                "ok": False,
                "msg": f"Falha ao salvar rascunho: {exc}",
            }

    # Remove um rascunho de cliente.
    @_requer_autenticacao
    def excluir_rascunho_cliente(self, rascunho_id):
        try:
            removido = excluir_rascunho_cliente(rascunho_id)
            return _serializar_para_front(
                {
                    "ok": True,
                    "removido": removido,
                    "itens": listar_rascunhos_clientes(),
                }
            )
        except Exception as exc:
            return {
                "ok": False,
                "msg": f"Falha ao excluir rascunho: {exc}",
            }

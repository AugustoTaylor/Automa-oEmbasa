from __future__ import annotations

import calendar
import re
import time
from collections import defaultdict
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from backend.flows.common import ler_status_texto, notificar_progresso
from backend.utils.sap_waits import (
    element_exists,
    wait_for_element,
    wait_until_not_exists,
    wait_until_ready,
)


ARVORE_MENU_ID = "wnd[0]/usr/cntlIMAGE_CONTAINER/shellcont/shell/shellcont[0]/shell"
BOTAO_SELECAO_MULTIPLA_ID = "wnd[0]/usr/btn%_SD_SAKNR_%_APP_%-VALU_PUSH"
RADIO_TODAS_PARTIDAS_ID = "wnd[0]/usr/radX_AISEL"
DATA_INICIAL_ID = "wnd[0]/usr/ctxtSO_BUDAT-LOW"
DATA_FINAL_ID = "wnd[0]/usr/ctxtSO_BUDAT-HIGH"
CAMPO_BUSCA_ID = "wnd[1]/usr/txtRSYSF-STRING"

_CENTAVOS = Decimal("0.01")
_COORDENADAS_LABEL_RE = re.compile(r"lbl\[(\d+),(\d+)\]", re.IGNORECASE)
_CONTA_SUBTOTAL_RE = re.compile(r"\*\*\s*CONTA\s+(\d{8})", re.IGNORECASE)
_VALOR_SAP_RE = re.compile(r"(?<!\d)(\d{1,3}(?:\.\d{3})*|\d+),(\d{2})(-?)")
_SEM_RESULTADO_INDICADORES = (
    "NAO ENCONTR",
    "NÃO ENCONTR",
    "NOT FOUND",
    "NO HITS",
    "NENHUMA OCORRENCIA",
    "NENHUMA OCORRÊNCIA",
)


def periodo_mensal_sap(mes, ano) -> tuple[str, str]:
    try:
        mes_numero = int(mes)
        ano_numero = int(ano)
    except (TypeError, ValueError) as exc:
        raise ValueError("Informe mes e ano validos para consultar o SAP.") from exc

    if mes_numero < 1 or mes_numero > 12:
        raise ValueError("O mes da consulta deve estar entre 1 e 12.")

    if ano_numero < 2020 or ano_numero > 2099:
        raise ValueError("O ano da consulta deve estar entre 2020 e 2099.")

    ultimo_dia = calendar.monthrange(ano_numero, mes_numero)[1]
    # Inicio em branco: considerar todo o historico da conta ate esta data.
    return "", f"{ultimo_dia:02d}{mes_numero:02d}{ano_numero:04d}"


def parsear_valor_sap(valor) -> Decimal:
    texto = str(valor or "").strip().replace(" ", "")

    if not texto:
        raise ValueError("Valor SAP vazio.")

    negativo = texto.endswith("-")

    if negativo:
        texto = texto[:-1]

    texto = texto.replace(".", "").replace(",", ".")

    try:
        numero = Decimal(texto)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"Valor SAP invalido: {valor}") from exc

    if negativo:
        numero = -numero

    return numero.quantize(_CENTAVOS, rounding=ROUND_HALF_UP)


def _propriedade_texto(componente, *nomes) -> str:
    for nome in nomes:
        try:
            valor = getattr(componente, nome)
        except Exception:
            continue

        if valor is not None:
            return str(valor)

    return ""


def _iterar_filhos(componente):
    try:
        filhos = componente.Children
        total = int(filhos.Count)
    except Exception:
        return

    for indice in range(total):
        try:
            yield filhos(indice)
            continue
        except Exception:
            pass

        try:
            yield filhos.Item(indice)
        except Exception:
            continue


def extrair_saldos_de_componentes(componentes, contas_esperadas=None) -> dict[str, Decimal]:
    linhas = defaultdict(list)
    linha_sem_coordenada = 100000

    for componente in componentes or ():
        texto = _propriedade_texto(componente, "Text", "text").strip()

        if not texto:
            continue

        identificador = _propriedade_texto(componente, "Id", "id")
        coordenadas = _COORDENADAS_LABEL_RE.search(identificador)

        if coordenadas:
            coluna = int(coordenadas.group(1))
            linha = int(coordenadas.group(2))
        else:
            coluna = 0
            linha = linha_sem_coordenada
            linha_sem_coordenada += 1

        linhas[linha].append((coluna, texto))

    esperadas = {
        str(conta).strip()
        for conta in (contas_esperadas or ())
        if str(conta).strip()
    }
    saldos = {}

    for itens in linhas.values():
        texto_linha = " ".join(texto for _, texto in sorted(itens)).strip()
        conta_encontrada = _CONTA_SUBTOTAL_RE.search(texto_linha)

        if not conta_encontrada:
            continue

        conta = conta_encontrada.group(1)

        if esperadas and conta not in esperadas:
            continue

        trecho_valor = texto_linha[conta_encontrada.end() :]
        valor_encontrado = _VALOR_SAP_RE.search(trecho_valor)

        if not valor_encontrado:
            continue

        valor_texto = (
            f"{valor_encontrado.group(1)},{valor_encontrado.group(2)}"
            f"{valor_encontrado.group(3)}"
        )
        valor = parsear_valor_sap(valor_texto)

        if conta in saldos and saldos[conta] != valor:
            raise RuntimeError(
                f"O SAP retornou dois subtotais diferentes para a conta {conta}."
            )

        saldos[conta] = valor

    return saldos


def _posicao_scroll(area_usuario) -> tuple[int, int, int] | None:
    try:
        scrollbar = area_usuario.VerticalScrollbar
        posicao = int(scrollbar.Position)
        maximo = int(scrollbar.Maximum)
        pagina = max(1, int(getattr(scrollbar, "PageSize", 1) or 1))
        return posicao, maximo, pagina
    except Exception:
        return None


def extrair_saldos_popup(area_usuario, contas_esperadas=None, *, progress_callback=None) -> dict[str, Decimal]:
    """Le os subtotais da busca no SAP, incluindo linhas fora da area visivel.

    Nao pressupoe que as contas estao ordenadas e nao converte resultados
    ausentes em zero. Se a leitura nao estiver completa, informa o erro.
    """
    esperadas = {str(conta).strip() for conta in (contas_esperadas or ())}
    saldos = {}
    posicoes_visitadas = set()
    scrollbar = None

    try:
        scrollbar = area_usuario.VerticalScrollbar
        # Garante que o primeiro subtotal nao ficou acima da posicao atual.
        scrollbar.Position = 0
    except Exception:
        scrollbar = None

    for _ in range(100):
        componentes = list(_iterar_filhos(area_usuario) or ())
        pagina = extrair_saldos_de_componentes(componentes, esperadas)

        for conta, valor in pagina.items():
            if conta in saldos and saldos[conta] != valor:
                raise RuntimeError(
                    f"O SAP retornou dois subtotais diferentes para a conta {conta}."
                )
            saldos[conta] = valor

        if esperadas:
            notificar_progresso(
                progress_callback,
                "SAP_SALDOS",
                "running",
                f"Lendo subtotais: {len(saldos)} de {len(esperadas)} contas.",
                75 + int(20 * len(saldos) / len(esperadas)),
            )
            if len(saldos) == len(esperadas):
                break

        if scrollbar is None:
            break

        estado_scroll = _posicao_scroll(area_usuario)
        if estado_scroll is None:
            break

        posicao, maximo, pagina_tamanho = estado_scroll
        if posicao >= maximo or posicao in posicoes_visitadas:
            break

        posicoes_visitadas.add(posicao)
        proxima_posicao = min(maximo, posicao + max(1, pagina_tamanho - 2))
        if proxima_posicao == posicao:
            break

        try:
            scrollbar.Position = proxima_posicao
            # Pequena pausa para o SAP renderizar as linhas apos a rolagem.
            time.sleep(0.08)
        except Exception:
            break

    if esperadas:
        ausentes = sorted(esperadas - saldos.keys())
        if ausentes:
            raise RuntimeError(
                f"Leitura incompleta da janela Procurar: {len(saldos)} de "
                f"{len(esperadas)} contas capturadas. "
                f"Nao foi gerada planilha para evitar saldos incorretos. "
                f"Contas nao lidas: {', '.join(ausentes)}."
            )

    return saldos


def _copiar_contas_para_clipboard(texto: str) -> None:
    try:
        import win32clipboard  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "O componente de clipboard do Windows nao esta disponivel."
        ) from exc

    ultimo_erro = None

    for _ in range(10):
        abriu = False

        try:
            win32clipboard.OpenClipboard(None)
            abriu = True
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardText(texto, win32clipboard.CF_UNICODETEXT)
            return
        except Exception as exc:
            ultimo_erro = exc
            time.sleep(0.1)
        finally:
            if abriu:
                try:
                    win32clipboard.CloseClipboard()
                except Exception:
                    pass

    raise RuntimeError(f"Nao foi possivel copiar as contas: {ultimo_erro}")


def _fechar_janela(session, indice: int) -> None:
    for elemento in (
        f"wnd[{indice}]/tbar[0]/btn[12]",
        f"wnd[{indice}]/tbar[0]/btn[0]",
    ):
        try:
            session.findById(elemento).press()
            wait_until_ready(session, timeout=3)
            return
        except Exception:
            continue

    try:
        session.findById(f"wnd[{indice}]").sendVKey(12)
    except Exception:
        pass


def _preparar_tela_contas(session) -> None:
    """Reentra na selecao do relatorio apos uma consulta anterior.

    Nao depende de retornar ao menu manualmente entre contas individuais.
    Falha explicitamente se a navegacao nao tiver sido concluida.
    """
    principal = wait_for_element(session, "wnd[0]", timeout=30)
    principal.maximize()
    for _ in range(10):
        if element_exists(session, BOTAO_SELECAO_MULTIPLA_ID):
            return
        if element_exists(session, ARVORE_MENU_ID):
            arvore = wait_for_element(session, ARVORE_MENU_ID, timeout=10)
            arvore.selectedNode = "F00025"
            arvore.doubleClickNode("F00025")
            if not wait_until_ready(session, timeout=30):
                raise TimeoutError("O SAP nao abriu a selecao do relatorio.")
            if element_exists(session, BOTAO_SELECAO_MULTIPLA_ID):
                return
        else:
            # Na lista de lancamentos, F3 retorna a tela de selecao.
            principal.sendVKey(3)
            if not wait_until_ready(session, timeout=30):
                raise TimeoutError("O SAP nao retornou ao filtro de contas.")
    raise RuntimeError("Nao foi possivel acessar a tela de selecao de contas SAP.")


def _limpar_selecao_anterior(janela_multisselecao) -> None:
    """Limpa contas persistidas entre consultas antes de colar o proximo lote.

    A selecao multipla do SAP GUI utiliza o botao [16] para remover linhas
    anteriores em telas padrao. Verifica se ele existe antes de acioná-lo;
    ausencia impede o proximo lote para evitar misturar contas.
    """
    try:
        botao = janela_multisselecao.findById("tbar[0]/btn[16]")
        botao.press()
    except Exception as exc:
        raise RuntimeError(
            "Nao foi possivel limpar a selecao anterior do SAP. "
            "Confira o botao Excluir tudo do popup de selecao multipla."
        ) from exc


def executar_consulta_saldos_sap(
    session,
    contas,
    mes,
    ano,
    *,
    logger=None,
    progress_callback=None,
    clipboard_writer=None,
    limpar_contas_anteriores=False,
) -> dict:
    contas_ordenadas = tuple(str(conta or "").strip() for conta in contas)

    if (not contas_ordenadas
            or any(not re.fullmatch(r"\d{8}", conta) for conta in contas_ordenadas)
            or len(set(contas_ordenadas)) != len(contas_ordenadas)):
        raise ValueError("A lista de contas do relatorio e invalida ou contem duplicatas.")

    data_inicial, data_final = periodo_mensal_sap(mes, ano)
    copiar_clipboard = clipboard_writer or _copiar_contas_para_clipboard

    notificar_progresso(
        progress_callback,
        "SAP_SALDOS",
        "running",
        f"Abrindo o relatorio e enviando {len(contas_ordenadas)} contas.",
        10,
    )

    _preparar_tela_contas(session)
    janela_principal = wait_for_element(session, "wnd[0]", timeout=15)
    wait_for_element(session, BOTAO_SELECAO_MULTIPLA_ID, timeout=12).press()
    janela_multisselecao = wait_for_element(session, "wnd[1]", timeout=8)
    if limpar_contas_anteriores:
        _limpar_selecao_anterior(janela_multisselecao)
    copiar_clipboard("\r\n".join(contas_ordenadas))
    janela_multisselecao.sendVKey(24)
    wait_until_ready(session, timeout=8)
    janela_multisselecao.sendVKey(8)
    wait_until_not_exists(session, "wnd[1]", timeout=8)

    wait_for_element(session, RADIO_TODAS_PARTIDAS_ID, timeout=8).select()
    campo_data_inicial = wait_for_element(session, DATA_INICIAL_ID, timeout=8)
    campo_data_final = wait_for_element(session, DATA_FINAL_ID, timeout=8)
    # Remover qualquer data inicial persistida na tela de selecao.
    campo_data_inicial.text = ""
    campo_data_final.text = data_final
    campo_data_final.setFocus()
    campo_data_final.caretPosition = len(data_final)

    notificar_progresso(
        progress_callback,
        "SAP_SALDOS",
        "running",
        f"Consultando todo o historico ate {data_final[:2]}/{data_final[2:4]}/{data_final[4:]}. A abertura pode demorar.",
        45,
    )
    janela_principal.sendVKey(8)

    if not wait_until_ready(session, timeout=600):
        raise TimeoutError("O SAP nao concluiu a consulta em 10 minutos.")

    # SAP pode liberar Busy antes de terminar de montar a tela do relatorio.
    # O campo da data final existe na selecao, mas nao na lista de lancamentos.
    limite_relatorio = time.monotonic() + 180
    while element_exists(session, DATA_FINAL_ID):
        if time.monotonic() >= limite_relatorio:
            status = ler_status_texto(session)
            raise TimeoutError(
                "O SAP nao abriu a lista de lancamentos apos a consulta. "
                f"Verifique mensagens ou filtros do SAP. Status: {status}"
            )
        time.sleep(0.25)

    notificar_progresso(
        progress_callback, "SAP_SALDOS", "running",
        "Relatorio carregado; localizando as contas e subtotais.", 60,
    )
    janela_principal = wait_for_element(session, "wnd[0]", timeout=15)
    janela_principal.sendVKey(80)
    if not wait_until_ready(session, timeout=30):
        raise TimeoutError("O SAP nao concluiu a preparacao da busca de contas.")
    janela_principal.sendVKey(71)

    campo_busca = wait_for_element(session, CAMPO_BUSCA_ID, timeout=30)
    campo_busca.text = "** Conta"
    campo_busca.caretPosition = len("** Conta")
    wait_for_element(session, "wnd[1]", timeout=5).sendVKey(0)
    if not wait_until_ready(session, timeout=180):
        raise TimeoutError("O SAP demorou mais de 3 minutos para listar os subtotais.")

    notificar_progresso(
        progress_callback, "SAP_SALDOS", "running",
        "Lendo os resultados da janela Procurar (com rolagem).", 75,
    )
    try:
        area_resultados = wait_for_element(session, "wnd[2]/usr", timeout=180)
    except Exception:
        status = ler_status_texto(session).upper()

        if any(indicador in status for indicador in _SEM_RESULTADO_INDICADORES):
            saldos = {}
        else:
            raise RuntimeError(
                "O SAP nao abriu a lista de subtotais da busca por '** Conta'."
            )
    else:
        saldos = extrair_saldos_popup(
            area_resultados, contas_ordenadas,
            progress_callback=progress_callback,
        )
    finally:
        if element_exists(session, "wnd[2]"):
            _fechar_janela(session, 2)
        if element_exists(session, "wnd[1]"):
            _fechar_janela(session, 1)

    ausentes = sorted(set(contas_ordenadas) - saldos.keys())
    if ausentes:
        raise RuntimeError(
            "Consulta incompleta; nenhuma planilha sera salva. "
            f"Contas sem subtotal confirmado: {', '.join(ausentes)}."
        )

    if logger is not None:
        logger.add(
            -1,
            f"SAP retornou {len(saldos)} subtotais; "
            f"{sum(saldo == 0 for saldo in saldos.values())} saldo(s) igual(is) a zero.",
            publico=True,
        )

    notificar_progresso(
        progress_callback,
        "SAP_SALDOS",
        "done",
        f"{len(saldos)} subtotais localizados no SAP.",
        100,
    )

    return {
        "saldos": saldos,
        "data_inicial": data_inicial,
        "data_final": data_final,
        "contas_consultadas": len(contas_ordenadas),
        "contas_retornadas": len(saldos),
        "contas_sem_movimento": 0,
        "contas_zeradas": sum(valor == 0 for valor in saldos.values()),
    }

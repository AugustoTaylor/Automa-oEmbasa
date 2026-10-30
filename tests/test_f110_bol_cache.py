from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from backend.flows import f110, f110_bol_cache


class ElementoFake:
    def __init__(self, text=""):
        self.text = text
        self.select_calls = 0

    def select(self):
        self.select_calls += 1


class JanelaFake:
    def __init__(self):
        self.teclas = []

    def sendVKey(self, tecla):
        self.teclas.append(tecla)


class SessaoFake:
    def __init__(self):
        self.janela = JanelaFake()

    def findById(self, element_id):
        if element_id == "wnd[0]":
            return self.janela
        raise KeyError(element_id)


class MemoriaFake:
    def __init__(self, inicio, origem="memoria"):
        self.inicio = inicio
        self.origem = origem
        self.tentativas = []

    def proximo_numero(self):
        return self.inicio

    def registrar_tentativa(self, numero):
        self.tentativas.append(numero)


class LoggerFake:
    def __init__(self):
        self.mensagens = []

    def add(self, etapa, mensagem, **kwargs):
        self.mensagens.append((etapa, mensagem, kwargs))


class F110BolCacheTests(unittest.TestCase):
    def test_historico_inicial_bol08_faz_proxima_execucao_comecar_no_bol09(self):
        with tempfile.TemporaryDirectory() as temporario:
            caminho = Path(temporario) / "ultimo_bol.json"

            with (
                patch.object(f110_bol_cache, "_cache_path", return_value=caminho),
                patch.object(f110_bol_cache, "_ultimo_bol_historico", return_value=8),
            ):
                with f110_bol_cache.memoria_bol("29102026") as memoria:
                    self.assertEqual(memoria.proximo_numero(), 9)
                    self.assertEqual(memoria.origem, "historico")
                    memoria.registrar_tentativa(9)

            payload = json.loads(caminho.read_text(encoding="utf-8"))
            registro = payload["datas"]["29102026"]
            self.assertEqual(registro["ultimo_confirmado"], 8)
            self.assertEqual(registro["ultima_tentativa"], 9)

    def test_tentativa_nao_confirmada_e_reutilizada_e_confirmada_avanca(self):
        with tempfile.TemporaryDirectory() as temporario:
            caminho = Path(temporario) / "ultimo_bol.json"

            with (
                patch.object(f110_bol_cache, "_cache_path", return_value=caminho),
                patch.object(f110_bol_cache, "_ultimo_bol_historico", return_value=8),
            ):
                with f110_bol_cache.memoria_bol("29102026") as memoria:
                    self.assertEqual(memoria.proximo_numero(), 9)
                    memoria.registrar_tentativa(9)

                with f110_bol_cache.memoria_bol("29102026") as memoria:
                    self.assertEqual(memoria.proximo_numero(), 9)
                    self.assertEqual(memoria.origem, "tentativa_pendente")

                self.assertTrue(
                    f110_bol_cache.confirmar_identificacao("29102026", "BOL09")
                )

                with f110_bol_cache.memoria_bol("29102026") as memoria:
                    self.assertEqual(memoria.proximo_numero(), 10)
                    self.assertEqual(memoria.origem, "memoria")

    def test_memoria_e_separada_por_data_de_execucao(self):
        with tempfile.TemporaryDirectory() as temporario:
            caminho = Path(temporario) / "ultimo_bol.json"

            with (
                patch.object(f110_bol_cache, "_cache_path", return_value=caminho),
                patch.object(f110_bol_cache, "_ultimo_bol_historico", return_value=0),
            ):
                with f110_bol_cache.memoria_bol("29102026") as memoria:
                    memoria.confirmar(8)

                with f110_bol_cache.memoria_bol("30102026") as memoria:
                    self.assertEqual(memoria.proximo_numero(), 1)
                    self.assertEqual(memoria.origem, "inicial")

    def test_reserva_recente_de_outro_processo_avanca_para_evitar_colisao(self):
        payload = {
            "schema": 1,
            "datas": {
                "29102026": {
                    "ultimo_confirmado": 8,
                    "ultima_tentativa": 9,
                    "reserva_owner": "outro-computador:999",
                    "reserva_em": datetime.now().isoformat(timespec="seconds"),
                }
            },
        }
        memoria = f110_bol_cache.MemoriaBol(
            payload,
            "29102026",
            Path("ultimo_bol.json"),
            owner="este-computador:123",
        )

        with patch.object(
            f110_bol_cache,
            "get_setting",
            side_effect=lambda *keys, default=None: 600
            if keys == ("f110", "reservation_ttl_seconds")
            else default,
        ):
            self.assertEqual(memoria.proximo_numero(), 10)
            self.assertEqual(memoria.origem, "reserva_ativa")

    def test_confirmacao_antiga_nao_apaga_reserva_de_bol_mais_novo(self):
        payload = {
            "schema": 1,
            "datas": {
                "29102026": {
                    "ultimo_confirmado": 8,
                    "ultima_tentativa": 10,
                    "reserva_owner": "outro-computador:999",
                    "reserva_em": datetime.now().isoformat(timespec="seconds"),
                }
            },
        }
        memoria = f110_bol_cache.MemoriaBol(
            payload,
            "29102026",
            Path("ultimo_bol.json"),
            owner="este-computador:123",
        )

        memoria.confirmar(9)
        registro = payload["datas"]["29102026"]
        self.assertEqual(registro["ultimo_confirmado"], 9)
        self.assertEqual(registro["ultima_tentativa"], 10)
        self.assertEqual(registro["reserva_owner"], "outro-computador:999")

    def test_leitura_do_historico_considera_maior_bol_do_dia(self):
        registros = [
            {"data": "2026-10-29", "numero_bol": "Projeto Água: BOL08 | Projeto Esgoto: BOL09"},
            {"data": "2026-10-29", "numero_bol": "BOL07"},
            {"data": "2026-10-28", "numero_bol": "BOL99"},
        ]

        with patch.object(
            f110_bol_cache,
            "listar_historico_pedidos",
            return_value=registros,
        ):
            self.assertEqual(f110_bol_cache._ultimo_bol_historico("29102026"), 9)

    def _executar_geracao(self, clientes_por_bol, inicio=9):
        sessao = SessaoFake()
        campo_data = ElementoFake()
        campo_ident = ElementoFake()
        aba_parametros = ElementoFake()
        aba_status = ElementoFake()
        memoria = MemoriaFake(inicio)
        logger = LoggerFake()

        def localizar(_session, element_id, timeout=0):
            if element_id == f110._CAMPO_DATA_EXEC:
                return campo_data
            if element_id == f110._CAMPO_IDENT:
                return campo_ident
            if element_id == f110._ABA_PAR:
                return aba_parametros
            if element_id == f110._ABA_STA:
                return aba_status
            if element_id == f110._CAMPO_PAR_CLIENTE:
                return ElementoFake(clientes_por_bol.get(campo_ident.text, ""))
            raise KeyError(element_id)

        @contextmanager
        def memoria_contexto(_data_exec):
            yield memoria

        with (
            patch.object(f110, "memoria_bol", side_effect=memoria_contexto),
            patch.object(f110, "wait_for_element", side_effect=localizar),
            patch.object(f110, "wait_until_ready", return_value=True),
        ):
            identificacao = f110.gerar_identificacao(
                sessao,
                "29102026",
                logger=logger,
            )

        return identificacao, memoria, logger, sessao

    def test_sap_recebe_diretamente_bol09_quando_memoria_indica_bol08(self):
        identificacao, memoria, logger, sessao = self._executar_geracao({"BOL09": ""})

        self.assertEqual(identificacao, "BOL09")
        self.assertEqual(memoria.tentativas, [9])
        self.assertEqual(sessao.janela.teclas, [0])
        self.assertTrue(any("BOL09" in mensagem for _, mensagem, _ in logger.mensagens))

    def test_bol_memorizado_ocupado_avanca_sem_voltar_ao_bol01(self):
        identificacao, memoria, _, sessao = self._executar_geracao(
            {"BOL09": "CLIENTE", "BOL10": ""}
        )

        self.assertEqual(identificacao, "BOL10")
        self.assertEqual(memoria.tentativas, [10])
        self.assertEqual(sessao.janela.teclas, [0, 0])


if __name__ == "__main__":
    unittest.main()

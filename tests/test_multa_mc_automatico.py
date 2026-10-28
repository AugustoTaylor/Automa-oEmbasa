"""Regras do cadastro/ativação do cliente para Multa Contratual.

Testes sem conexão ao SAP: não criam clientes nem ordens reais.
"""
import unittest
from unittest.mock import MagicMock, patch

from backend.flows import cliente_cadastro, va01, xd03


class MultaCadastroTestes(unittest.TestCase):
    def test_va01_usa_parametros_da_tela_sap(self):
        dados = va01._TIPOS_VA01["multa_contratual"]
        self.assertEqual(
            {campo: dados[campo] for campo in (
                "tipo_ordem", "canal", "setor", "escritorio", "equipe"
            )},
            {"tipo_ordem": "ZMTC", "canal": "MC", "setor": "MC",
             "escritorio": "1010", "equipe": "CAB"},
        )
        configuracao = cliente_cadastro.SETOR_CONFIG["MC"]
        self.assertEqual(configuracao["canal"], "MC")
        self.assertEqual(configuracao["vendas"], dados["escritorio"])
        self.assertEqual(configuracao["grupo"], dados["equipe"])

    def test_setor_mc_em_area_correta(self):
        valido = [{"codigo": "MC", "organizacao_vendas": "EMBA",
                   "canal_distribuicao": "MC"}]
        errado = [{"codigo": "MC", "organizacao_vendas": "EMBA",
                   "canal_distribuicao": "PO"}]
        outro = [{"codigo": "AG", "organizacao_vendas": "EMBA",
                  "canal_distribuicao": "PO"}]
        self.assertTrue(xd03._validar_suporte_tipo(valido, "multa_contratual"))
        self.assertFalse(xd03._validar_suporte_tipo(errado, "multa_contratual"))
        self.assertFalse(xd03._validar_suporte_tipo(outro, "multa_contratual"))
        self.assertTrue(xd03._validar_suporte_tipo(outro, "agua"))
        # Compatibilidade para SAP sem leitura dos campos adicionais.
        self.assertTrue(xd03._validar_suporte_tipo([{"codigo": "MC"}], "multa_contratual"))

    def test_novo_cliente_multa_inicia_setor_mc(self):
        with patch.object(cliente_cadastro, "criar_cliente") as criar:
            cliente_cadastro.criar_cliente_multa_contratual(
                object(), {"doc": "12345678900", "tipo": "agua"}, object()
            )
        self.assertEqual(criar.call_args.args[1]["tipo"], "multa_contratual")
        self.assertEqual(criar.call_args.args[1]["setores"], ("MC",))
        self.assertEqual(criar.call_args.kwargs["setor_inicial"], "MC")

    def test_setor_de_cliente_existente_usa_canal_mc_e_dados_vendas(self):
        session = object()
        logger = MagicMock()
        with (
            patch.object(cliente_cadastro, "_abrir_xd01") as abrir,
            patch.object(cliente_cadastro, "_preencher_primeira_tela") as primeira,
            patch.object(cliente_cadastro, "_preencher_dados_vendas") as vendas,
            patch.object(cliente_cadastro, "_salvar_cliente_ou_setor") as salvar,
        ):
            resposta = cliente_cadastro.adicionar_setores_cliente(
                session, {"cliente": "12345", "doc": "12345678900",
                          "tipo": "multa_contratual", "setores": ["MC"]}, logger,
            )
        self.assertTrue(resposta["ok"], resposta)
        abrir.assert_called_once()
        self.assertEqual(primeira.call_args.args[1], "PF01")
        self.assertEqual(primeira.call_args.args[2], "MC")
        self.assertEqual(primeira.call_args.kwargs["canal"], "MC")
        self.assertFalse(primeira.call_args.kwargs["incluir_empresa"])
        vendas.assert_called_once_with(session, "MC", incluir_imposto=False)
        salvar.assert_called_once()


if __name__ == "__main__":
    unittest.main()

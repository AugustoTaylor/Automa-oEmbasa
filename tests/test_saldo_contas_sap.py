from __future__ import annotations

import unittest
from decimal import Decimal

from backend.flows.saldo_contas_sap import (
    extrair_saldos_de_componentes,
    parsear_valor_sap,
    periodo_mensal_sap,
)


class _Label:
    def __init__(self, identificador, texto):
        self.Id = identificador
        self.Text = texto


class SaldoContasSapTests(unittest.TestCase):
    def test_periodo_mensal_considera_dias_do_mes_e_ano_bissexto(self):
        self.assertEqual(periodo_mensal_sap(9, 2026), ("01092026", "30092026"))
        self.assertEqual(periodo_mensal_sap(2, 2026), ("01022026", "28022026"))
        self.assertEqual(periodo_mensal_sap(2, 2028), ("01022028", "29022028"))
        self.assertEqual(periodo_mensal_sap(12, 2026), ("01122026", "31122026"))

    def test_parseia_valor_brasileiro_e_sinal_negativo_no_final(self):
        self.assertEqual(parsear_valor_sap("10.755.536,35"), Decimal("10755536.35"))
        self.assertEqual(parsear_valor_sap("5.956.699,14-"), Decimal("-5956699.14"))
        self.assertEqual(parsear_valor_sap("25,01-"), Decimal("-25.01"))
        self.assertEqual(parsear_valor_sap("4,98"), Decimal("4.98"))

    def test_extrai_conta_e_valor_de_labels_agrupados_por_linha(self):
        labels = [
            _Label("wnd[2]/usr/lbl[1,3]", "** Conta 11370003"),
            _Label("wnd[2]/usr/lbl[67,3]", "17.131,51"),
            _Label("wnd[2]/usr/lbl[75,3]", "BRL"),
            _Label("wnd[2]/usr/lbl[1,4]", "** Conta 11370004"),
            _Label("wnd[2]/usr/lbl[67,4]", "10.755.536,35-"),
            _Label("wnd[2]/usr/lbl[75,4]", "BRL"),
            _Label("wnd[2]/usr/lbl[1,5]", "Cabecalho sem subtotal"),
        ]

        resultado = extrair_saldos_de_componentes(
            labels,
            contas_esperadas=("11370003", "11370004", "11370005"),
        )

        self.assertEqual(
            resultado,
            {
                "11370003": Decimal("17131.51"),
                "11370004": Decimal("-10755536.35"),
            },
        )


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path

from openpyxl import Workbook

from backend.relatorios.saldo_contas import (
    CONTAS_APLICACAO_MODELO,
    analisar_planilha_saldo_contas,
)


class SaldoContasPlanilhaTests(unittest.TestCase):
    def _criar_planilha(self, pasta: Path, *, alterar_primeira_conta=False, vazia=False):
        caminho = pasta / "saldo-contas.xlsx"
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "SET.26"
        sheet["C1"] = date(2026, 9, 1)
        sheet["C19"] = "APLIC. MOVIMENTO"
        sheet["D19"] = "Individuais"

        total = 0

        for indice, conta in enumerate(CONTAS_APLICACAO_MODELO, start=20):
            sheet.cell(indice, 3).value = (
                "99999999" if alterar_primeira_conta and indice == 20 else int(conta)
            )
            if not vazia:
                valor = indice * 10.25
                sheet.cell(indice, 4).value = valor
                total += valor

        sheet["B59"] = "=D59+D60"
        sheet["C61"] = total if not vazia else None
        sheet["D61"] = "=SUM(D20:D60)"
        sheet["J5"] = "=D61-J6"
        sheet["J6"] = "=B59"
        sheet["J7"] = "=J3+J4+J5+J6"
        workbook.save(caminho)
        workbook.close()
        return caminho

    def test_modelo_valido_com_41_contas(self):
        with tempfile.TemporaryDirectory() as temporario:
            caminho = self._criar_planilha(Path(temporario))
            resultado = analisar_planilha_saldo_contas(caminho)

        self.assertTrue(resultado["ok"])
        self.assertTrue(resultado["modelo_valido"])
        self.assertEqual(resultado["periodo"]["data"], "2026-09-01")
        self.assertEqual(resultado["resumo"]["contas_encontradas"], 41)
        self.assertEqual(resultado["resumo"]["saldos_preenchidos"], 41)
        self.assertEqual(resultado["resumo"]["conferencia"], "ok")

    def test_conta_diferente_bloqueia_modelo(self):
        with tempfile.TemporaryDirectory() as temporario:
            caminho = self._criar_planilha(
                Path(temporario),
                alterar_primeira_conta=True,
            )
            resultado = analisar_planilha_saldo_contas(caminho)

        self.assertTrue(resultado["ok"])
        self.assertFalse(resultado["modelo_valido"])
        self.assertTrue(any("Contas ausentes" in aviso for aviso in resultado["avisos"]))
        self.assertTrue(any("Contas diferentes" in aviso for aviso in resultado["avisos"]))

    def test_saldo_vazio_nao_e_convertido_em_zero(self):
        with tempfile.TemporaryDirectory() as temporario:
            caminho = self._criar_planilha(Path(temporario), vazia=True)
            resultado = analisar_planilha_saldo_contas(caminho)

        self.assertTrue(resultado["modelo_valido"])
        self.assertEqual(resultado["resumo"]["saldos_preenchidos"], 0)
        self.assertEqual(resultado["resumo"]["saldos_vazios"], 41)
        self.assertTrue(any("saldo(s) vazio(s)" in aviso for aviso in resultado["avisos"]))

    def test_exige_aba_set_26(self):
        with tempfile.TemporaryDirectory() as temporario:
            caminho = Path(temporario) / "sem-modelo.xlsx"
            workbook = Workbook()
            workbook.active.title = "OUT.26"
            workbook.save(caminho)
            workbook.close()
            resultado = analisar_planilha_saldo_contas(caminho)

        self.assertFalse(resultado["ok"])
        self.assertIn("SET.26", resultado["msg"])


if __name__ == "__main__":
    unittest.main()

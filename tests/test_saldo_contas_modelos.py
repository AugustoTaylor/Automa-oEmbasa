from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.relatorios import saldo_contas_modelos


class SaldoContasModelosTests(unittest.TestCase):
    def _modelo(self):
        return {
            "id": "",
            "nome": "Modelo editavel",
            "aba": "OUT.26",
            "celula_periodo": "A1",
            "grupos": [
                {
                    "id": "abacate",
                    "nome": "ABACATE MOVIMENTO",
                    "ativo": True,
                    "celula_cabecalho": "A3",
                    "celula_total": "A6",
                    "contas": [
                        {
                            "conta": "11311001",
                            "celula_conta": "A4",
                            "celula_valor": "B4",
                            "modo": "lote",
                            "ativo": True,
                        }
                    ],
                }
            ],
        }

    def test_salva_edita_lista_e_exclui_modelo(self):
        with tempfile.TemporaryDirectory() as temporario:
            arquivo = Path(temporario) / "modelos.json"
            with patch.object(saldo_contas_modelos, "_arquivo_modelos", return_value=arquivo):
                salvo = saldo_contas_modelos.salvar_modelo_saldo_contas(self._modelo())
                self.assertTrue(salvo["id"])
                self.assertEqual(len(saldo_contas_modelos.listar_modelos_saldo_contas()), 1)

                salvo["nome"] = "Modelo renomeado"
                salvo["grupos"][0]["contas"][0]["modo"] = "individual"
                editado = saldo_contas_modelos.salvar_modelo_saldo_contas(salvo)
                self.assertEqual(editado["id"], salvo["id"])
                self.assertEqual(
                    saldo_contas_modelos.obter_modelo_saldo_contas(salvo["id"])["nome"],
                    "Modelo renomeado",
                )
                self.assertTrue(saldo_contas_modelos.excluir_modelo_saldo_contas(salvo["id"]))
                self.assertEqual(saldo_contas_modelos.listar_modelos_saldo_contas(), [])

    def test_conta_de_timeout_permanece_desativada_no_modelo(self):
        modelo = self._modelo()
        modelo["grupos"][0]["contas"].append(
            {
                "conta": "11311000",
                "celula_conta": "A5",
                "celula_valor": "B5",
                "modo": "lote",
                "ativo": True,
            }
        )
        with tempfile.TemporaryDirectory() as temporario:
            arquivo = Path(temporario) / "modelos.json"
            with patch.object(saldo_contas_modelos, "_arquivo_modelos", return_value=arquivo):
                salvo = saldo_contas_modelos.salvar_modelo_saldo_contas(modelo)

        conta = salvo["grupos"][0]["contas"][1]
        self.assertFalse(conta["ativo"])
        self.assertTrue(conta["bloqueada"])


if __name__ == "__main__":
    unittest.main()

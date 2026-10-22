import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.history import (
    listar_historico_pedidos,
    obter_historico_pedido,
    registrar_historico_saldo_contas,
)


class HistoricoSaldoContasTests(unittest.TestCase):
    def test_registra_resumo_contas_e_txt(self):
        with tempfile.TemporaryDirectory() as temporario:
            raiz = Path(temporario)

            def setting(section, key, default=None):
                valores = {
                    ("history", "enabled"): True,
                    ("history", "shared_dir"): str(raiz),
                    ("history", "db_file"): "historico.json",
                    ("history", "max_entries"): 100,
                    ("history", "max_results"): 100,
                    ("history", "lock_timeout_seconds"): 1,
                }
                return valores.get((section, key), default)

            payload = {
                "mes": "09",
                "ano": 2026,
                "data_final": "30092026",
                "arquivo_origem": "modelo.xlsx",
                "arquivo_saida": "modelo_PREENCHIDO.xlsx",
                "caminho_saida": str(raiz / "modelo_PREENCHIDO.xlsx"),
                "consultas_executadas": 2,
                "contas_individuais": 1,
                "duracao_segundos": 65.4,
                "total": 1234.56,
                "grupos": [
                    {"id": "centralizadora", "nome": "CENTRALIZADORA", "quantidade": 1, "total": 1234.56}
                ],
                "contas": [
                    {
                        "conta": "11311305",
                        "grupo": "CENTRALIZADORA",
                        "celula": "B23",
                        "modo_consulta": "individual",
                        "valor": 1234.56,
                    }
                ],
            }

            with patch("backend.history.get_setting", side_effect=setting):
                registro = registrar_historico_saldo_contas(payload)
                self.assertIsNotNone(registro)
                self.assertEqual(registro["tipo_registro"], "saldo_contas")
                self.assertEqual(registro["periodo"], "09/2026")
                self.assertEqual(registro["contas"][0]["valor_formatado"], "R$ 1.234,56")

                banco = json.loads((raiz / "historico.json").read_text(encoding="utf-8"))
                self.assertEqual(len(banco["registros"]), 1)
                self.assertTrue(Path(registro["txt_path"]).exists())
                self.assertIn("11311305", Path(registro["txt_path"]).read_text(encoding="utf-8"))

                resumo = listar_historico_pedidos(filtro="11311305")
                self.assertEqual(len(resumo), 1)
                self.assertEqual(resumo[0]["tipo_registro"], "saldo_contas")
                completo = obter_historico_pedido(registro["id"])
                self.assertEqual(completo["arquivo_saida"], "modelo_PREENCHIDO.xlsx")


if __name__ == "__main__":
    unittest.main()

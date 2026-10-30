from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.history import (
    diagnostico_historico,
    listar_historico_pedidos,
    obter_historico_pedido,
    registrar_historico_saldo_contas,
)
from interface import API


class HistoricoPorUsuarioTests(unittest.TestCase):
    def _setting(self, raiz: Path):
        valores = {
            ("history", "enabled"): True,
            ("history", "shared_dir"): str(raiz),
            ("history", "db_file"): "historico.json",
            ("history", "max_entries"): 100,
            ("history", "max_results"): 100,
            ("history", "lock_timeout_seconds"): 1,
        }

        def obter(section, key, default=None):
            return valores.get((section, key), default)

        return obter

    @staticmethod
    def _payload(conta: str) -> dict:
        return {
            "mes": "10",
            "ano": 2026,
            "arquivo_saida": f"saldo_{conta}.xlsx",
            "contas": [
                {
                    "conta": conta,
                    "grupo": "GRUPO",
                    "celula": "B2",
                    "modo_consulta": "lote",
                    "valor": 10,
                }
            ],
            "grupos": [
                {
                    "id": "grupo",
                    "nome": "GRUPO",
                    "quantidade": 1,
                    "total": 10,
                }
            ],
            "total": 10,
        }

    def test_bancos_listas_detalhes_e_txt_ficam_separados_por_matricula(self):
        with tempfile.TemporaryDirectory() as temporario:
            raiz = Path(temporario)

            with patch(
                "backend.history.get_setting",
                side_effect=self._setting(raiz),
            ):
                registro_a = registrar_historico_saldo_contas(
                    self._payload("11370001"),
                    matricula_usuario="E111111",
                )
                registro_b = registrar_historico_saldo_contas(
                    self._payload("11370002"),
                    matricula_usuario="E222222",
                )

                self.assertEqual(registro_a["matricula_usuario"], "e111111")
                self.assertEqual(registro_b["matricula_usuario"], "e222222")

                diagnostico_a = diagnostico_historico("e111111")
                diagnostico_b = diagnostico_historico("e222222")
                self.assertNotEqual(
                    diagnostico_a["db_file"],
                    diagnostico_b["db_file"],
                )
                self.assertTrue(Path(diagnostico_a["db_file"]).exists())
                self.assertTrue(Path(diagnostico_b["db_file"]).exists())
                self.assertEqual(diagnostico_a["total"], 1)
                self.assertEqual(diagnostico_b["total"], 1)

                lista_a = listar_historico_pedidos(
                    matricula_usuario="E111111"
                )
                lista_b = listar_historico_pedidos(
                    matricula_usuario="e222222"
                )
                self.assertEqual([item["id"] for item in lista_a], [registro_a["id"]])
                self.assertEqual([item["id"] for item in lista_b], [registro_b["id"]])
                self.assertEqual(
                    obter_historico_pedido(
                        registro_a["id"],
                        matricula_usuario="e111111",
                    )["id"],
                    registro_a["id"],
                )
                self.assertIsNone(
                    obter_historico_pedido(
                        registro_a["id"],
                        matricula_usuario="e222222",
                    )
                )

                todos_internos = listar_historico_pedidos()
                self.assertEqual(len(todos_internos), 2)

                texto = Path(registro_a["txt_path"]).read_text(encoding="utf-8")
                self.assertIn("Matrícula do sistema: e111111", texto)
                self.assertIn("E111111", Path(registro_a["txt_path"]).parts)

                legado = {
                    "schema": 1,
                    "registros": [
                        {
                            "id": "registro_legado",
                            "data_hora": "2026-01-01T10:00:00",
                            "numero_pedido": "LEGADO",
                        }
                    ],
                }
                (raiz / "historico.json").write_text(
                    json.dumps(legado),
                    encoding="utf-8",
                )
                self.assertEqual(
                    len(listar_historico_pedidos(matricula_usuario="e111111")),
                    1,
                )
                self.assertEqual(len(listar_historico_pedidos()), 3)

    def test_api_usa_matricula_autenticada_para_lista_e_detalhe(self):
        api = API.__new__(API)
        api._usuario_autenticado = {"matricula": "E123456"}

        with patch("interface.listar_historico_pedidos", return_value=[]) as listar:
            resposta = api.listar_historico("cliente", 25)

        self.assertTrue(resposta["ok"])
        listar.assert_called_once_with(
            filtro="cliente",
            limite=25,
            matricula_usuario="e123456",
        )

        with patch("interface.obter_historico_pedido", return_value=None) as obter:
            resposta = api.obter_historico("id-de-outro-usuario")

        self.assertFalse(resposta["ok"])
        obter.assert_called_once_with(
            "id-de-outro-usuario",
            matricula_usuario="e123456",
        )


if __name__ == "__main__":
    unittest.main()

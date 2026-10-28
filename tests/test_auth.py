from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend import auth
from interface import API


class AutenticacaoTests(unittest.TestCase):
    def test_cadastro_salva_hash_e_login_valida_senha(self):
        with tempfile.TemporaryDirectory() as temporario:
            banco = Path(temporario) / "usuarios.json"
            lembrar = Path(temporario) / "lembrar.dat"
            with (
                patch.object(auth, "_arquivo_usuarios", return_value=banco),
                patch.object(auth, "_arquivo_lembrar_local", return_value=lembrar),
            ):
                cadastrado = auth.cadastrar_usuario("001234", "Senha123", versao_app="1.8.0")
                self.assertTrue(cadastrado["ok"])
                payload = json.loads(banco.read_text(encoding="utf-8"))
                registro = payload["usuarios"][0]
                self.assertNotIn("Senha123", banco.read_text(encoding="utf-8"))
                self.assertEqual(registro["senha"]["algoritmo"], "pbkdf2_sha256")
                self.assertGreaterEqual(registro["senha"]["iteracoes"], 300_000)

                login = auth.autenticar_usuario("001234", "Senha123", versao_app="1.8.0")
                self.assertTrue(login["ok"])
                with self.assertRaisesRegex(ValueError, "invalidas"):
                    auth.autenticar_usuario("001234", "SenhaErrada1")

    def test_lembrar_usa_token_e_checkin_sem_regravar_senha(self):
        token_local = {}
        with tempfile.TemporaryDirectory() as temporario:
            banco = Path(temporario) / "usuarios.json"
            lembrar = Path(temporario) / "lembrar.dat"

            def proteger(payload):
                token_local.clear()
                token_local.update(payload)

            with (
                patch.object(auth, "_arquivo_usuarios", return_value=banco),
                patch.object(auth, "_arquivo_lembrar_local", return_value=lembrar),
                patch.object(auth, "_proteger_local", side_effect=proteger),
                patch.object(auth, "_ler_local_protegido", side_effect=lambda: dict(token_local) or None),
            ):
                cadastrado = auth.cadastrar_usuario(
                    "009876",
                    "Acesso987",
                    lembrar=True,
                    versao_app="1.8.0",
                )
                self.assertTrue(cadastrado["lembrado"])
                self.assertIn("segredo", token_local)
                banco_texto = banco.read_text(encoding="utf-8")
                self.assertNotIn(token_local["segredo"], banco_texto)

                checkin = auth.verificar_acesso_lembrado(versao_app="1.8.1")
                self.assertTrue(checkin["ok"])
                payload = json.loads(banco.read_text(encoding="utf-8"))
                self.assertEqual(payload["usuarios"][0]["ultima_versao"], "1.8.1")

    def test_senha_fraca_e_matricula_invalida_sao_bloqueadas(self):
        with self.assertRaisesRegex(ValueError, "matricula"):
            auth.cadastrar_usuario("12", "Senha123")
        with self.assertRaisesRegex(ValueError, "8 caracteres"):
            auth.cadastrar_usuario("1234", "A1b")
        with self.assertRaisesRegex(ValueError, "letras e numeros"):
            auth.cadastrar_usuario("1234", "12345678")

    def test_falha_ao_lembrar_nao_bloqueia_cadastro(self):
        with tempfile.TemporaryDirectory() as temporario:
            banco = Path(temporario) / "usuarios.json"
            lembrar = Path(temporario) / "lembrar.dat"
            with (
                patch.object(auth, "_arquivo_usuarios", return_value=banco),
                patch.object(auth, "_arquivo_lembrar_local", return_value=lembrar),
                patch.object(auth, "_proteger_local", side_effect=RuntimeError("DPAPI indisponivel")),
            ):
                resultado = auth.cadastrar_usuario(
                    "005555",
                    "Senha555",
                    lembrar=True,
                    versao_app="1.8.0",
                )
                self.assertTrue(resultado["ok"])
                self.assertFalse(resultado["lembrado"])
                self.assertIn("lembrete seguro", resultado["aviso"])
                payload = json.loads(banco.read_text(encoding="utf-8"))
                self.assertEqual(payload["usuarios"][0]["tokens"], [])

    def test_operacao_backend_exige_sessao_autenticada(self):
        api = API.__new__(API)
        api._usuario_autenticado = None
        resultado = api.listar_modelos_saldo_contas()
        self.assertFalse(resultado["ok"])
        self.assertTrue(resultado["auth_required"])


if __name__ == "__main__":
    unittest.main()

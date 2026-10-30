from __future__ import annotations

import json
import sys
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
                cadastrado = auth.cadastrar_usuario("E397156", "Senha123", versao_app="1.8.3")
                self.assertTrue(cadastrado["ok"])
                self.assertTrue(cadastrado["login_required"])
                self.assertFalse(cadastrado["lembrado"])
                payload = json.loads(banco.read_text(encoding="utf-8"))
                registro = payload["usuarios"][0]
                self.assertEqual(registro["matricula"], "e397156")
                self.assertEqual(registro["ultimo_login"], "")
                self.assertEqual(registro["ultimo_checkin"], "")
                self.assertEqual(registro["tokens"], [])
                self.assertNotIn("Senha123", banco.read_text(encoding="utf-8"))
                self.assertNotIn(
                    cadastrado["chave_recuperacao"],
                    banco.read_text(encoding="utf-8"),
                )
                self.assertTrue(registro["recuperacao"]["hash"])
                self.assertEqual(registro["senha"]["algoritmo"], "pbkdf2_sha256")
                self.assertGreaterEqual(registro["senha"]["iteracoes"], 300_000)

                login = auth.autenticar_usuario("E397156", "Senha123", versao_app="1.8.3")
                self.assertTrue(login["ok"])
                with self.assertRaisesRegex(ValueError, "invalidas"):
                    auth.autenticar_usuario("e397156", "SenhaErrada1")

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
                cadastrado = auth.cadastrar_usuario("e09876", "Acesso987", versao_app="1.8.3")
                self.assertFalse(cadastrado["lembrado"])
                self.assertEqual(token_local, {})
                login = auth.autenticar_usuario(
                    "e09876",
                    "Acesso987",
                    lembrar=True,
                    versao_app="1.8.3",
                )
                self.assertTrue(login["lembrado"])
                self.assertIn("segredo", token_local)
                banco_texto = banco.read_text(encoding="utf-8")
                self.assertNotIn(token_local["segredo"], banco_texto)

                checkin = auth.verificar_acesso_lembrado(versao_app="1.8.3")
                self.assertTrue(checkin["ok"])
                payload = json.loads(banco.read_text(encoding="utf-8"))
                self.assertEqual(payload["usuarios"][0]["ultima_versao"], "1.8.3")

    @unittest.skipUnless(sys.platform == "win32", "DPAPI disponivel somente no Windows")
    def test_lembrar_faz_roundtrip_real_com_protecao_do_windows(self):
        with tempfile.TemporaryDirectory() as temporario:
            lembrar = Path(temporario) / "lembrar.dat"
            token = {
                "matricula": "e123456",
                "token_id": "token-teste",
                "segredo": "segredo-aleatorio",
            }

            with patch.object(auth, "_arquivo_lembrar_local", return_value=lembrar):
                auth._proteger_local(token)
                recuperado = auth._ler_local_protegido()

            self.assertEqual(recuperado, token)
            self.assertTrue(lembrar.exists())
            self.assertNotIn(b"segredo-aleatorio", lembrar.read_bytes())

    def test_recuperar_acesso_troca_senha_revoga_tokens_e_gira_chave(self):
        with tempfile.TemporaryDirectory() as temporario:
            banco = Path(temporario) / "usuarios.json"
            lembrar = Path(temporario) / "lembrar.dat"

            with (
                patch.object(auth, "_arquivo_usuarios", return_value=banco),
                patch.object(auth, "_arquivo_lembrar_local", return_value=lembrar),
            ):
                cadastrado = auth.cadastrar_usuario("e45678", "Senha123")
                chave_original = cadastrado["chave_recuperacao"]
                recuperado = auth.recuperar_acesso(
                    "e45678",
                    chave_original.lower(),
                    "NovaSenha987",
                    versao_app="1.8.11",
                )

                self.assertTrue(recuperado["ok"])
                self.assertTrue(recuperado["login_required"])
                self.assertNotEqual(
                    recuperado["chave_recuperacao"],
                    chave_original,
                )
                with self.assertRaisesRegex(ValueError, "invalidas"):
                    auth.autenticar_usuario("e45678", "Senha123")
                login = auth.autenticar_usuario("e45678", "NovaSenha987")
                self.assertTrue(login["ok"])

                payload = json.loads(banco.read_text(encoding="utf-8"))
                registro = payload["usuarios"][0]
                self.assertEqual(registro["tokens"], [])
                self.assertNotIn(chave_original, banco.read_text(encoding="utf-8"))
                self.assertNotIn(
                    recuperado["chave_recuperacao"],
                    banco.read_text(encoding="utf-8"),
                )

    def test_acesso_lembrado_permite_recuperar_sem_digitar_chave(self):
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
                patch.object(
                    auth,
                    "_ler_local_protegido",
                    side_effect=lambda: dict(token_local) or None,
                ),
                patch.object(
                    auth,
                    "_remover_lembranca_local",
                    side_effect=token_local.clear,
                ),
            ):
                auth.cadastrar_usuario("e76543", "Senha123")
                login = auth.autenticar_usuario("e76543", "Senha123", lembrar=True)
                self.assertTrue(login["lembrado"])

                recuperado = auth.recuperar_acesso(
                    "e76543",
                    "",
                    "SenhaNova456",
                )
                self.assertTrue(recuperado["ok"])
                self.assertEqual(token_local, {})
                self.assertTrue(
                    auth.autenticar_usuario("e76543", "SenhaNova456")["ok"]
                )

    def test_conta_antiga_recebe_chave_no_primeiro_login_apos_atualizacao(self):
        with tempfile.TemporaryDirectory() as temporario:
            banco = Path(temporario) / "usuarios.json"
            lembrar = Path(temporario) / "lembrar.dat"

            with (
                patch.object(auth, "_arquivo_usuarios", return_value=banco),
                patch.object(auth, "_arquivo_lembrar_local", return_value=lembrar),
            ):
                auth.cadastrar_usuario("e11223", "Senha123")
                payload = json.loads(banco.read_text(encoding="utf-8"))
                payload["usuarios"][0].pop("recuperacao")
                banco.write_text(json.dumps(payload), encoding="utf-8")

                login = auth.autenticar_usuario("e11223", "Senha123")
                self.assertTrue(login["chave_recuperacao"])
                atualizado = json.loads(banco.read_text(encoding="utf-8"))
                self.assertTrue(atualizado["usuarios"][0]["recuperacao"]["hash"])

    def test_senha_fraca_e_matricula_invalida_sao_bloqueadas(self):
        with self.assertRaisesRegex(ValueError, "4 e 7 caracteres"):
            auth.cadastrar_usuario("abc", "Senha123")
        with self.assertRaisesRegex(ValueError, "4 e 7 caracteres"):
            auth.cadastrar_usuario("abcdefgh", "Senha123")
        with self.assertRaisesRegex(ValueError, "8 caracteres"):
            auth.cadastrar_usuario("e123", "A1b")
        with self.assertRaisesRegex(ValueError, "letras e numeros"):
            auth.cadastrar_usuario("e123", "12345678")

    def test_cadastro_nao_emite_token_nem_autentica_api(self):
        with tempfile.TemporaryDirectory() as temporario:
            banco = Path(temporario) / "usuarios.json"
            lembrar = Path(temporario) / "lembrar.dat"
            with (
                patch.object(auth, "_arquivo_usuarios", return_value=banco),
                patch.object(auth, "_arquivo_lembrar_local", return_value=lembrar),
                patch.object(auth, "_proteger_local") as proteger,
            ):
                api = API.__new__(API)
                api._usuario_autenticado = {"matricula": "anterior"}
                resultado = api.cadastrar_usuario({
                    "matricula": "e55555",
                    "senha": "Senha555",
                    "lembrar": True,
                    "versao_app": "1.8.3",
                })
                self.assertTrue(resultado["ok"])
                self.assertFalse(resultado["lembrado"])
                self.assertTrue(resultado["login_required"])
                self.assertIsNone(api._usuario_autenticado)
                proteger.assert_not_called()
                payload = json.loads(banco.read_text(encoding="utf-8"))
                self.assertEqual(payload["usuarios"][0]["tokens"], [])

    def test_operacao_backend_exige_sessao_autenticada(self):
        api = API.__new__(API)
        api._usuario_autenticado = None
        resultado = api.listar_modelos_saldo_contas()
        self.assertFalse(resultado["ok"])
        self.assertTrue(resultado["auth_required"])

    def test_api_recupera_mas_exige_login_com_a_nova_senha(self):
        with tempfile.TemporaryDirectory() as temporario:
            banco = Path(temporario) / "usuarios.json"
            lembrar = Path(temporario) / "lembrar.dat"

            with (
                patch.object(auth, "_arquivo_usuarios", return_value=banco),
                patch.object(auth, "_arquivo_lembrar_local", return_value=lembrar),
            ):
                cadastro = auth.cadastrar_usuario("e99887", "Senha123")
                api = API.__new__(API)
                api._usuario_autenticado = {"matricula": "anterior"}
                resposta = api.recuperar_acesso(
                    {
                        "matricula": "e99887",
                        "chave_recuperacao": cadastro["chave_recuperacao"],
                        "nova_senha": "NovaSenha123",
                        "versao_app": "1.8.11",
                    }
                )

                self.assertTrue(resposta["ok"])
                self.assertTrue(resposta["login_required"])
                self.assertIsNone(api._usuario_autenticado)


if __name__ == "__main__":
    unittest.main()

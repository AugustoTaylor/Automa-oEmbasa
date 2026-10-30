from __future__ import annotations

import base64
import unittest
from unittest.mock import patch

from backend import support_mail


class FakeSMTP:
    instances = []

    def __init__(self, host, port, timeout):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.ehlo_calls = 0
        self.starttls_calls = 0
        self.login_calls = []
        self.messages = []
        self.__class__.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def ehlo(self):
        self.ehlo_calls += 1

    def starttls(self, context):
        self.starttls_calls += 1

    def login(self, username, password):
        self.login_calls.append((username, password))

    def send_message(self, message):
        self.messages.append(message)
        return {}


class FailingSMTP(FakeSMTP):
    def __init__(self, host, port, timeout):
        raise OSError("servidor indisponível")


class SupportMailTests(unittest.TestCase):
    def setUp(self):
        FakeSMTP.instances.clear()

    @staticmethod
    def _payload(imagens=None):
        return support_mail.normalize_support_request(
            {
                "matricula": "E397156",
                "nome": "Usuário de Teste",
                "lotacao": "FAFT",
                "setor": "Tesouraria",
                "descricao": "Preciso de apoio com a automação.",
                "imagens": imagens or [],
            }
        )

    @staticmethod
    def _config(**overrides):
        config = {
            "smtp_host": "smtp.interno.embasa.ba.gov.br",
            "smtp_port": 25,
            "smtp_use_tls": True,
            "smtp_username": "",
            "smtp_from_email": "automacao-api@embasa.ba.gov.br",
            "smtp_from_name": "EMBASA - Fale Conosco",
            "subject_prefix": "[EMBASA - Fale Conosco]",
        }
        config.update(overrides)
        return config

    def test_smtp_direto_nao_exige_conta_e_destino_nao_pode_ser_sobrescrito(self):
        config = self._config(destination_email="destino-alterado@exemplo.com")

        with patch.object(support_mail.smtplib, "SMTP", FakeSMTP):
            result = support_mail._send_support_email_smtp(
                self._payload(),
                "FAFTA-20261029-120000",
                config,
            )

        smtp = FakeSMTP.instances[0]
        self.assertEqual(smtp.host, "smtp.interno.embasa.ba.gov.br")
        self.assertEqual(smtp.port, 25)
        self.assertEqual(smtp.starttls_calls, 1)
        self.assertEqual(smtp.login_calls, [])
        self.assertEqual(len(smtp.messages), 1)
        self.assertEqual(
            smtp.messages[0]["To"],
            support_mail.SUPPORT_DESTINATION_EMAIL,
        )
        self.assertEqual(result["destination_email"], "faft@embasa.ba.gov.br")

    def test_mensagem_contem_identificacao_protocolo_e_anexo(self):
        content = b"imagem-de-teste"
        attachment = {
            "name": "erro.png",
            "size": len(content),
            "type": "image/png",
            "content_base64": base64.b64encode(content).decode("ascii"),
        }

        with patch.object(support_mail.smtplib, "SMTP", FakeSMTP):
            support_mail._send_support_email_smtp(
                self._payload([attachment]),
                "FAFTA-20261029-120001",
                self._config(),
            )

        message = FakeSMTP.instances[0].messages[0]
        self.assertEqual(message["Auto-Submitted"], "auto-generated")
        self.assertEqual(message["X-Auto-Response-Suppress"], "All")
        self.assertIn("FAFTA-20261029-120001", message["Subject"])
        self.assertIn("E397156", message.get_body(preferencelist=("plain",)).get_content())
        attachments = list(message.iter_attachments())
        self.assertEqual(len(attachments), 1)
        self.assertEqual(attachments[0].get_filename(), "erro.png")
        self.assertEqual(attachments[0].get_payload(decode=True), content)

    def test_senha_smtp_so_pode_vir_de_variavel_de_ambiente(self):
        config = self._config(
            smtp_username="conta-servico@embasa.ba.gov.br",
            smtp_password="senha-que-nao-deve-ser-usada",
            smtp_password_env="EMBASA_TEST_SUPPORT_PASSWORD",
        )

        with (
            patch.dict(support_mail.os.environ, {}, clear=True),
            patch.object(support_mail.smtplib, "SMTP", FakeSMTP),
            self.assertRaisesRegex(ValueError, "EMBASA_TEST_SUPPORT_PASSWORD"),
        ):
            support_mail._send_support_email_smtp(
                self._payload(),
                "FAFTA-20261029-120002",
                config,
            )

        self.assertEqual(FakeSMTP.instances, [])

    def test_modo_padrao_automatico_despacha_por_smtp(self):
        config = self._config(
            notification_email_enabled=True,
            delivery_mode="smtp_direct",
        )

        with (
            patch.object(support_mail, "_support_config", return_value=config),
            patch.object(support_mail.smtplib, "SMTP", FakeSMTP),
        ):
            result = support_mail.send_support_emails(
                self._payload(),
                "FAFTA-20261029-120003",
            )

        self.assertEqual(result["delivery_mode"], "smtp")
        self.assertEqual(result["destination_email"], "faft@embasa.ba.gov.br")

    def test_falha_de_rede_retorna_mensagem_clara(self):
        with (
            patch.object(support_mail.smtplib, "SMTP", FailingSMTP),
            self.assertRaisesRegex(ValueError, "rede da EMBASA"),
        ):
            support_mail._send_support_email_smtp(
                self._payload(),
                "FAFTA-20261029-120004",
                self._config(),
            )


if __name__ == "__main__":
    unittest.main()

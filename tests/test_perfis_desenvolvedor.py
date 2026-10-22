import unittest
from unittest.mock import patch

from interface import API


class PerfisDesenvolvedorTests(unittest.TestCase):
    @patch("interface.os.startfile")
    def test_abre_linkedin_no_navegador_padrao(self, startfile):
        api = API.__new__(API)

        resultado = api.abrir_perfil_desenvolvedor("linkedin")

        self.assertTrue(resultado["success"])
        self.assertEqual(
            resultado["url"],
            "https://www.linkedin.com/in/augusto-taylor-425285364/",
        )
        startfile.assert_called_once_with(resultado["url"])

    @patch("interface.os.startfile")
    def test_abre_github_no_navegador_padrao(self, startfile):
        api = API.__new__(API)

        resultado = api.abrir_perfil_desenvolvedor("github")

        self.assertTrue(resultado["success"])
        self.assertEqual(resultado["url"], "https://github.com/AugustoTaylor")
        startfile.assert_called_once_with(resultado["url"])

    @patch("interface.os.startfile")
    def test_bloqueia_endereco_nao_cadastrado(self, startfile):
        api = API.__new__(API)

        resultado = api.abrir_perfil_desenvolvedor("outro")

        self.assertFalse(resultado["success"])
        startfile.assert_not_called()


if __name__ == "__main__":
    unittest.main()

import unittest
from unittest.mock import patch

from backend.flows import f110_boleto


def _linha(linha, spool, titulo, checkbox=True):
    texto_normalizado = titulo.upper()
    return {
        "linha": linha,
        "spool": str(spool),
        "titulo": titulo,
        "texto_normalizado": texto_normalizado,
        "checkbox_id": f"wnd[0]/usr/chk[1,{linha}]" if checkbox else "",
    }


class _ElementoFake:
    def __init__(self, selected=False):
        self.selected = selected
        self.caretPosition = 0

    def setFocus(self):
        return None

    def SetFocus(self):
        return None

    def maximize(self):
        return None


class _SessaoFake:
    def __init__(self, linhas):
        self.elementos = {"wnd[0]": _ElementoFake()}
        self.teclas = []

        for linha in linhas:
            y_linha = int(linha["linha"])
            checkbox_id = f"wnd[0]/usr/chk[1,{y_linha}]"
            label_id = f"wnd[0]/usr/lbl[51,{y_linha}]"
            self.elementos[checkbox_id] = _ElementoFake(selected=True)
            self.elementos[label_id] = _ElementoFake()

    def findById(self, element_id):
        return self.elementos[element_id]

    def sendVKey(self, tecla):
        self.teclas.append(tecla)


class F110Sp02AguaEsgotoTests(unittest.TestCase):
    def test_reencontra_esgoto_por_spool_e_pega_proximo_boleto_visual(self):
        referencia = _linha(3, 9003, "BOLETO (CONTAS A RECEBER)")
        linhas_reabertas = [
            _linha(12, 8998, "Nota acompanh.ISD &"),
            _linha(1, 9010, "BOLETO (CONTAS A RECEBER)"),
            _linha(9, 9001, "BOLETO (CONTAS A RECEBER)"),
            _linha(6, 9002, "Nota acompanh.ISD &"),
            _linha(5, 9003, "BOLETO (CONTAS A RECEBER)"),
            _linha(10, 9000, "Nota acompanh.ISD &"),
        ]

        esgoto, agua = f110_boleto._localizar_boleto_agua_apos_esgoto(
            linhas_reabertas,
            referencia,
        )

        self.assertEqual(esgoto["spool"], "9003")
        self.assertEqual(esgoto["linha"], 5)
        self.assertEqual(agua["spool"], "9001")
        self.assertEqual(agua["linha"], 9)

    def test_ignora_nota_entre_os_dois_boletos(self):
        referencia = _linha(2, 5002, "BOLETO (CONTAS A RECEBER)")
        linhas = [
            referencia,
            _linha(3, 5001, "Nota acompanh.ISD &"),
            _linha(4, 5000, "BOLETO (CONTAS A RECEBER)"),
            _linha(5, 4999, "Nota acompanh.ISD &"),
        ]

        _, agua = f110_boleto._localizar_boleto_agua_apos_esgoto(
            linhas,
            referencia,
        )

        self.assertEqual(agua["linha"], 4)

    def test_selecao_exclusiva_desmarca_esgoto_e_notas(self):
        linhas = [
            _linha(2, 5002, "BOLETO (CONTAS A RECEBER)"),
            _linha(3, 5001, "Nota acompanh.ISD &"),
            _linha(4, 5000, "BOLETO (CONTAS A RECEBER)"),
            _linha(5, 4999, "Nota acompanh.ISD &"),
        ]
        sessao = _SessaoFake(linhas)
        agua = linhas[2]

        with (
            patch.object(f110_boleto, "wait_for_element", side_effect=lambda session, element_id, timeout=0: session.findById(element_id)),
            patch.object(f110_boleto, "wait_until_ready", return_value=True),
            patch.object(f110_boleto.time, "sleep", return_value=None),
        ):
            resultado = f110_boleto._selecionar_unico_boleto_sp02(
                sessao,
                agua,
                linhas_visiveis=linhas,
            )

        self.assertEqual(resultado["spool"], "5000")
        self.assertFalse(sessao.elementos["wnd[0]/usr/chk[1,2]"].selected)
        self.assertFalse(sessao.elementos["wnd[0]/usr/chk[1,3]"].selected)
        self.assertTrue(sessao.elementos["wnd[0]/usr/chk[1,4]"].selected)
        self.assertFalse(sessao.elementos["wnd[0]/usr/chk[1,5]"].selected)


if __name__ == "__main__":
    unittest.main()

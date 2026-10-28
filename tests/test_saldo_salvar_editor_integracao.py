"""Gravacao e reabertura de grupos/contas editadas no armazenamento compartilhado."""
from __future__ import annotations
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch
from openpyxl import Workbook, load_workbook

from backend.relatorios import saldo_contas_modelos
from interface import API


class EditorModeloPersistenciaTests(unittest.TestCase):
    def test_edicoes_grupos_e_contas_sao_salvas_e_recuperadas(self):
        with tempfile.TemporaryDirectory() as tmp:
            pasta = Path(tmp)
            arquivo = pasta / 'consulta.xlsx'
            wb = Workbook()
            ws = wb.active
            ws.title = 'SET.26'
            ws['A1'] = date(2026, 9, 1)
            ws['A3'] = 'BCO CTA MOVIMENTOS'
            ws['A4'] = 11311025
            ws['A5'] = 11311026
            ws['C3'] = 'APLIC. MOVIMENTO'
            ws['C4'] = 11370003
            wb.save(arquivo)
            wb.close()
            modelo = {
                'id': '', 'nome': 'Saldo de contas - SET.26', 'aba': 'SET.26',
                'celula_periodo': 'A1',
                'grupos': [
                    {'id': 'movimentos', 'nome': 'BCO CTA MOVIMENTOS', 'ativo': True,
                     'celula_cabecalho': 'A3', 'celula_total': '', 'contas': [
                         {'conta': '11311025', 'celula_conta': 'A4', 'celula_valor': 'B4', 'modo': 'lote', 'ativo': True},
                         {'conta': '11311026', 'celula_conta': 'A5', 'celula_valor': 'B5', 'modo': 'lote', 'ativo': True}
                     ]}
                ]
            }
            api = API.__new__(API)
            api._usuario_autenticado = {'matricula': 'T12345'}
            caminho_modelos = pasta / 'compartilhado' / 'saldo_contas_modelos.json'
            with patch.object(saldo_contas_modelos, '_arquivo_modelos', return_value=caminho_modelos):
                resposta = api.salvar_modelo_saldo_contas({'caminho': str(arquivo), 'modelo': modelo})
                self.assertTrue(resposta['ok'], resposta.get('msg'))
                modelo_salvo = resposta['modelo']
                self.assertTrue(modelo_salvo['id'])
                # Mudancas feitas no editor: remove uma conta, desativa outra,
                # e adiciona um grupo com uma conta de consulta individual.
                modelo_salvo['grupos'][0]['contas'].pop()
                modelo_salvo['grupos'][0]['ativo'] = False
                modelo_salvo['grupos'].append({
                    'id': '', 'nome': 'APLIC. MOVIMENTO', 'ativo': True,
                    'celula_cabecalho': 'C3', 'celula_total': '',
                    'contas': [{'conta':'11370003', 'celula_conta':'C4',
                                'celula_valor':'D4', 'modo':'individual', 'ativo':True}]
                })
                resposta = api.salvar_modelo_saldo_contas({'caminho': str(arquivo), 'modelo': modelo_salvo})
                self.assertTrue(resposta['ok'], resposta.get('msg'))
                persistido = saldo_contas_modelos.obter_modelo_saldo_contas(modelo_salvo['id'])
                self.assertEqual(len(persistido['grupos']), 2)
                self.assertFalse(persistido['grupos'][0]['ativo'])
                self.assertEqual(len(persistido['grupos'][0]['contas']), 1)
                self.assertEqual(persistido['grupos'][1]['contas'][0]['modo'], 'individual')
                self.assertEqual(persistido['grupos'][1]['contas'][0]['celula_valor'], 'D4')
                self.assertEqual(len(saldo_contas_modelos.listar_modelos_saldo_contas()), 1,
                                 'editar um modelo nao pode criar duplicatas')
                # Erro de celula inexistente no Excel nao deve gravar alteracoes.
                modelo_invalido = dict(persistido)
                from copy import deepcopy
                modelo_invalido = deepcopy(persistido)
                modelo_invalido['grupos'][1]['contas'][0]['celula_conta'] = 'C5'
                resposta = api.salvar_modelo_saldo_contas({'caminho': str(arquivo), 'modelo': modelo_invalido})
                self.assertFalse(resposta['ok'])
                self.assertIn('C5', resposta['msg'])
                self.assertEqual(saldo_contas_modelos.obter_modelo_saldo_contas(modelo_salvo['id'])['grupos'][1]['contas'][0]['celula_conta'], 'C4')
            wb = load_workbook(arquivo)
            self.assertIsNone(wb['SET.26']['D4'].value, 'salvar modelo nao deve alterar saldos no Excel')
            wb.close()


if __name__ == '__main__':
    unittest.main()

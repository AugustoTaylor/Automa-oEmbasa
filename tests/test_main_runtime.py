from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import main


class RuntimeLocalTests(unittest.TestCase):
    def test_copia_local_nao_replica_dados_compartilhados(self):
        with tempfile.TemporaryDirectory() as temporario:
            raiz = Path(temporario)
            origem = raiz / "origem"
            destino = raiz / "destino"
            (origem / "dados_compartilhados" / "autenticacao").mkdir(parents=True)
            (origem / "dados_compartilhados" / "autenticacao" / "usuarios.json").write_text(
                '{"usuarios": []}',
                encoding="utf-8",
            )
            (origem / "aplicativo.txt").write_text("runtime", encoding="utf-8")

            with patch.object(main.shutil, "which", return_value=None):
                main.copy_runtime_to_local(origem, destino)

            self.assertTrue((destino / "aplicativo.txt").exists())
            self.assertFalse((destino / "dados_compartilhados").exists())


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import json
import os
import threading
import time
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from backend.relatorios.saldo_contas import normalizar_modelo_saldo_contas
from backend.settings import get_project_root, get_setting


_LOCK = threading.RLock()
_ARQUIVO = "saldo_contas_modelos.json"


def _agora_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _arquivo_modelos() -> Path:
    configurado = str(
        get_setting("saldo_contas", "shared_dir", default="dados_compartilhados/saldo_contas")
        or "dados_compartilhados/saldo_contas"
    ).strip()
    raiz = Path(configurado)
    if not raiz.is_absolute():
        origem_rede = str(os.environ.get("EMBASA_NETWORK_SOURCE") or "").strip()
        raiz = (Path(origem_rede) if origem_rede else get_project_root()) / raiz
    nome = str(get_setting("saldo_contas", "models_file", default=_ARQUIVO) or _ARQUIVO)
    return raiz / Path(nome).name


@contextmanager
def _file_lock(caminho: Path):
    caminho.parent.mkdir(parents=True, exist_ok=True)
    lock_path = caminho.with_suffix(caminho.suffix + ".lock")
    inicio = time.monotonic()
    fd = None
    while True:
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_RDWR)
            os.write(fd, str(os.getpid()).encode("ascii", errors="ignore"))
            break
        except FileExistsError:
            if time.monotonic() - inicio > 10:
                try:
                    if time.time() - lock_path.stat().st_mtime > 60:
                        lock_path.unlink()
                        continue
                except Exception:
                    pass
                raise TimeoutError("Os modelos compartilhados estao ocupados. Tente novamente.")
            time.sleep(0.08)
    try:
        yield
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except Exception:
                pass
        try:
            lock_path.unlink()
        except Exception:
            pass


def _ler_payload() -> dict:
    caminho = _arquivo_modelos()
    if not caminho.exists():
        return {"versao": 1, "modelos": []}
    try:
        payload = json.loads(caminho.read_text(encoding="utf-8-sig"))
    except Exception:
        return {"versao": 1, "modelos": []}
    if not isinstance(payload, dict) or not isinstance(payload.get("modelos"), list):
        return {"versao": 1, "modelos": []}
    return payload


def _salvar_payload(payload: dict) -> None:
    caminho = _arquivo_modelos()
    caminho.parent.mkdir(parents=True, exist_ok=True)
    temporario = caminho.with_name(f".{caminho.name}.{uuid4().hex}.tmp")
    temporario.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(temporario, caminho)


def listar_modelos_saldo_contas() -> list[dict]:
    with _LOCK:
        modelos = _ler_payload().get("modelos") or []
        return deepcopy(modelos)


def obter_modelo_saldo_contas(modelo_id) -> dict | None:
    chave = str(modelo_id or "").strip()
    if not chave:
        return None
    with _LOCK:
        for modelo in _ler_payload().get("modelos") or []:
            if str(modelo.get("id") or "") == chave:
                return deepcopy(modelo)
    return None


def salvar_modelo_saldo_contas(modelo) -> dict:
    normalizado = normalizar_modelo_saldo_contas(modelo)
    agora = _agora_iso()
    with _LOCK:
        with _file_lock(_arquivo_modelos()):
            payload = _ler_payload()
            modelos = payload.setdefault("modelos", [])
            modelo_id = str(normalizado.get("id") or "").strip() or uuid4().hex
            normalizado["id"] = modelo_id
            normalizado["atualizado_em"] = agora
            substituido = False
            for indice, existente in enumerate(modelos):
                if str(existente.get("id") or "") == modelo_id:
                    normalizado["criado_em"] = existente.get("criado_em") or agora
                    modelos[indice] = normalizado
                    substituido = True
                    break
            if not substituido:
                normalizado["criado_em"] = agora
                modelos.append(normalizado)
            payload["versao"] = 1
            _salvar_payload(payload)
        return deepcopy(normalizado)


def excluir_modelo_saldo_contas(modelo_id) -> bool:
    chave = str(modelo_id or "").strip()
    if not chave:
        return False
    with _LOCK:
        with _file_lock(_arquivo_modelos()):
            payload = _ler_payload()
            modelos = payload.get("modelos") or []
            restantes = [
                modelo for modelo in modelos
                if str(modelo.get("id") or "") != chave
            ]
            if len(restantes) == len(modelos):
                return False
            payload["modelos"] = restantes
            _salvar_payload(payload)
            return True

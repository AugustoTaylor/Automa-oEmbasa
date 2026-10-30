from __future__ import annotations

import json
import os
import re
import socket
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from backend.history import listar_historico_pedidos
from backend.settings import get_project_root, get_runtime_root, get_setting


_BOL_PATTERN = re.compile(r"\bBOL\s*0*(\d{1,3})\b", flags=re.IGNORECASE)
_MAX_BOL = 100


def _owner_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


def _numero_bol(valor) -> int:
    match = _BOL_PATTERN.search(str(valor or ""))

    if not match:
        return 0

    numero = int(match.group(1))
    return numero if 1 <= numero <= _MAX_BOL else 0


def _numeros_bol(valor) -> list[int]:
    numeros = []

    for match in _BOL_PATTERN.finditer(str(valor or "")):
        numero = int(match.group(1))

        if 1 <= numero <= _MAX_BOL:
            numeros.append(numero)

    return numeros


def _data_iso(data_exec: str) -> str:
    try:
        return datetime.strptime(str(data_exec or ""), "%d%m%Y").strftime("%Y-%m-%d")
    except ValueError:
        return ""


def _ultimo_bol_historico(data_exec: str) -> int:
    data = _data_iso(data_exec)

    if not data:
        return 0

    try:
        registros = listar_historico_pedidos(limite=5000)
    except Exception:
        return 0

    numeros = []

    for registro in registros:
        if str(registro.get("data") or "") != data:
            continue

        numeros.extend(_numeros_bol(registro.get("numero_bol")))

    return max(numeros, default=0)


def _resolver_raiz_compartilhada() -> Path:
    configurado = str(
        get_setting("f110", "shared_dir", default="dados_compartilhados/f110") or ""
    ).strip()
    raiz = Path(configurado or "dados_compartilhados/f110")

    if not raiz.is_absolute():
        origem_rede = str(os.environ.get("EMBASA_NETWORK_SOURCE") or "").strip()
        base = Path(origem_rede) if origem_rede else get_project_root()
        raiz = base / raiz

    return raiz


def _cache_root() -> Path:
    raiz = _resolver_raiz_compartilhada()

    try:
        raiz.mkdir(parents=True, exist_ok=True)
        return raiz
    except Exception:
        if not get_setting("f110", "fallback_to_local", default=True):
            raise

    fallback = get_runtime_root() / "f110"
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


def _cache_path() -> Path:
    nome = str(
        get_setting("f110", "bol_memory_file", default="ultimo_bol.json")
        or "ultimo_bol.json"
    )
    return _cache_root() / Path(nome).name


@contextmanager
def _file_lock(path: Path):
    lock_path = path.with_suffix(path.suffix + ".lock")
    timeout = float(get_setting("f110", "lock_timeout_seconds", default=180) or 180)
    stale_seconds = max(600.0, timeout * 3)
    deadline = time.monotonic() + max(5.0, timeout)
    fd = None

    while fd is None:
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_RDWR)
            identificacao = f"{socket.gethostname()}:{os.getpid()}"
            os.write(fd, identificacao.encode("utf-8", errors="ignore"))
        except FileExistsError:
            try:
                idade = time.time() - lock_path.stat().st_mtime
            except OSError:
                idade = 0

            if idade >= stale_seconds:
                try:
                    lock_path.unlink()
                except OSError:
                    pass
                continue

            if time.monotonic() >= deadline:
                raise TimeoutError(
                    "Outra execução está selecionando um número BOL. Aguarde a "
                    "conclusão e tente novamente."
                )

            time.sleep(0.1)

    try:
        yield
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass

        try:
            lock_path.unlink()
        except OSError:
            pass


def _ler_payload(path: Path) -> dict:
    if not path.exists():
        return {"schema": 1, "datas": {}}

    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return {"schema": 1, "datas": {}}

    if not isinstance(payload, dict):
        return {"schema": 1, "datas": {}}

    datas = payload.get("datas")
    payload["schema"] = 1
    payload["datas"] = datas if isinstance(datas, dict) else {}
    return payload


def _salvar_payload(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload["schema"] = 1
    payload["atualizado_em"] = datetime.now().isoformat(timespec="seconds")

    datas = payload.get("datas") if isinstance(payload.get("datas"), dict) else {}
    chaves_ordenadas = sorted(
        datas,
        key=lambda chave: str((datas.get(chave) or {}).get("atualizado_em") or chave),
    )
    payload["datas"] = {chave: datas[chave] for chave in chaves_ordenadas[-120:]}

    temp_path = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    temp_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temp_path.replace(path)


@dataclass
class MemoriaBol:
    payload: dict
    data_exec: str
    path: Path
    dirty: bool = False
    origem: str = "inicial"
    owner: str = field(default_factory=_owner_id)

    def _registro(self) -> dict:
        datas = self.payload.setdefault("datas", {})
        registro = datas.get(self.data_exec)

        if not isinstance(registro, dict):
            registro = {}
            datas[self.data_exec] = registro

        return registro

    def proximo_numero(self) -> int:
        registro = self._registro()
        confirmado = int(registro.get("ultimo_confirmado") or 0)
        tentativa = int(registro.get("ultima_tentativa") or 0)

        if tentativa > confirmado:
            reserva_owner = str(registro.get("reserva_owner") or "")
            reserva_em = str(registro.get("reserva_em") or "")
            ttl = float(
                get_setting("f110", "reservation_ttl_seconds", default=600) or 600
            )

            try:
                idade_reserva = (
                    datetime.now() - datetime.fromisoformat(reserva_em)
                ).total_seconds()
            except (TypeError, ValueError):
                idade_reserva = ttl + 1

            if reserva_owner and reserva_owner != self.owner and idade_reserva < ttl:
                self.origem = "reserva_ativa"
                return tentativa + 1

            self.origem = "tentativa_pendente"
            return tentativa

        if confirmado:
            self.origem = "memoria"
            return confirmado + 1

        historico = _ultimo_bol_historico(self.data_exec)

        if historico:
            registro["ultimo_confirmado"] = historico
            registro["ultima_tentativa"] = historico
            registro["atualizado_em"] = datetime.now().isoformat(timespec="seconds")
            self.dirty = True
            self.origem = "historico"
            return historico + 1

        self.origem = "inicial"
        return 1

    def registrar_tentativa(self, numero: int) -> None:
        registro = self._registro()
        registro["ultima_tentativa"] = int(numero)
        registro["identificacao_tentada"] = f"BOL{int(numero):02d}"
        registro["reserva_owner"] = self.owner
        registro["reserva_em"] = datetime.now().isoformat(timespec="seconds")
        registro["atualizado_em"] = datetime.now().isoformat(timespec="seconds")
        self.dirty = True

    def confirmar(self, numero: int) -> None:
        registro = self._registro()
        numero = int(numero)
        registro["ultimo_confirmado"] = max(
            numero,
            int(registro.get("ultimo_confirmado") or 0),
        )
        tentativa_atual = int(registro.get("ultima_tentativa") or 0)
        registro["ultima_tentativa"] = max(numero, tentativa_atual)
        registro["identificacao_confirmada"] = f"BOL{numero:02d}"

        if tentativa_atual <= numero:
            registro["reserva_owner"] = ""
            registro["reserva_em"] = ""

        registro["atualizado_em"] = datetime.now().isoformat(timespec="seconds")
        self.dirty = True


@contextmanager
def memoria_bol(data_exec: str):
    path = _cache_path()

    with _file_lock(path):
        memoria = MemoriaBol(_ler_payload(path), str(data_exec or ""), path)

        try:
            yield memoria
        finally:
            if memoria.dirty:
                _salvar_payload(path, memoria.payload)


def confirmar_identificacao(data_exec: str, identificacao: str) -> bool:
    numero = _numero_bol(identificacao)

    if not numero:
        return False

    with memoria_bol(data_exec) as memoria:
        memoria.confirmar(numero)

    return True

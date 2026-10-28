from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import socket
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from backend.settings import get_project_root, get_runtime_root, get_setting


_LOCK = threading.RLock()
_PBKDF2_ITERACOES = 390_000
_MAX_FALHAS = 5
_BLOQUEIO_MINUTOS = 15
_MAX_TOKENS_POR_USUARIO = 5
_MIN_MATRICULA_CARACTERES = 4
_MAX_MATRICULA_CARACTERES = 7


def _agora() -> datetime:
    return datetime.now(timezone.utc)


def _iso(tempo: datetime | None = None) -> str:
    return (tempo or _agora()).isoformat(timespec="seconds")


def _matricula(valor: Any) -> str:
    texto = str(valor or "").strip().casefold()
    if not _MIN_MATRICULA_CARACTERES <= len(texto) <= _MAX_MATRICULA_CARACTERES:
        raise ValueError("A matricula deve possuir entre 4 e 7 caracteres.")
    return texto


def _validar_senha(senha: Any) -> str:
    texto = str(senha or "")
    if len(texto) < 8:
        raise ValueError("A senha deve possuir pelo menos 8 caracteres.")
    if len(texto) > 128:
        raise ValueError("A senha ultrapassa o limite permitido.")
    if not any(caractere.isalpha() for caractere in texto) or not any(
        caractere.isdigit() for caractere in texto
    ):
        raise ValueError("A senha deve combinar letras e numeros.")
    return texto


def _base_compartilhada() -> Path:
    configurado = str(
        get_setting("auth", "shared_dir", default="dados_compartilhados/autenticacao") or ""
    ).strip()
    if not configurado:
        raise RuntimeError("A pasta compartilhada de autenticacao nao foi configurada.")
    caminho = Path(configurado)
    if not caminho.is_absolute():
        origem_rede = str(os.environ.get("EMBASA_NETWORK_SOURCE") or "").strip()
        base = Path(origem_rede) if origem_rede else get_project_root()
        caminho = base / caminho
    try:
        caminho.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        raise RuntimeError(
            "A pasta compartilhada de autenticacao esta indisponivel. "
            "Abra o sistema pela pasta da rede e tente novamente."
        ) from exc
    return caminho


def _arquivo_usuarios() -> Path:
    nome = str(get_setting("auth", "db_file", default="usuarios.json") or "usuarios.json")
    return _base_compartilhada() / Path(nome).name


def _arquivo_lembrar_local() -> Path:
    return get_runtime_root() / "auth_lembrar.dat"


@contextmanager
def _file_lock(caminho: Path):
    lock_path = caminho.with_suffix(caminho.suffix + ".lock")
    inicio = time.monotonic()
    fd = None
    while True:
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_RDWR)
            os.write(fd, f"{os.getpid()}|{socket.gethostname()}".encode("utf-8"))
            break
        except FileExistsError:
            if time.monotonic() - inicio > 10:
                try:
                    if time.time() - lock_path.stat().st_mtime > 60:
                        lock_path.unlink()
                        continue
                except Exception:
                    pass
                raise TimeoutError("O cadastro compartilhado esta ocupado. Tente novamente.")
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


def _ler_banco() -> dict:
    caminho = _arquivo_usuarios()
    if not caminho.exists():
        return {"versao": 1, "usuarios": []}
    try:
        payload = json.loads(caminho.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        raise RuntimeError("O cadastro compartilhado de usuarios esta corrompido.") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("usuarios"), list):
        raise RuntimeError("O formato do cadastro compartilhado de usuarios e invalido.")
    return payload


def _salvar_banco(payload: dict) -> None:
    caminho = _arquivo_usuarios()
    temporario = caminho.with_name(f".{caminho.name}.{uuid4().hex}.tmp")
    temporario.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(temporario, caminho)


def _hash_senha(senha: str, salt: bytes, iteracoes: int = _PBKDF2_ITERACOES) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", senha.encode("utf-8"), salt, int(iteracoes))


def _novo_registro_senha(senha: str) -> dict:
    salt = secrets.token_bytes(16)
    digest = _hash_senha(senha, salt)
    return {
        "algoritmo": "pbkdf2_sha256",
        "iteracoes": _PBKDF2_ITERACOES,
        "salt": base64.b64encode(salt).decode("ascii"),
        "hash": base64.b64encode(digest).decode("ascii"),
    }


def _senha_confere(senha: str, registro: dict) -> bool:
    try:
        salt = base64.b64decode(registro["salt"], validate=True)
        esperado = base64.b64decode(registro["hash"], validate=True)
        calculado = _hash_senha(senha, salt, int(registro["iteracoes"]))
        return hmac.compare_digest(calculado, esperado)
    except Exception:
        return False


def _token_hash(segredo: str) -> str:
    return hashlib.sha256(segredo.encode("utf-8")).hexdigest()


def _proteger_local(payload: dict) -> None:
    try:
        import win32crypt
    except ImportError as exc:
        raise RuntimeError("A protecao segura de credenciais do Windows nao esta disponivel.") from exc
    dados = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    protegido = win32crypt.CryptProtectData(
        dados,
        "EMBASA acesso lembrado",
        None,
        None,
        None,
        0,
    )[1]
    caminho = _arquivo_lembrar_local()
    caminho.parent.mkdir(parents=True, exist_ok=True)
    caminho.write_bytes(base64.b64encode(protegido))


def _ler_local_protegido() -> dict | None:
    caminho = _arquivo_lembrar_local()
    if not caminho.exists():
        return None
    try:
        import win32crypt
        protegido = base64.b64decode(caminho.read_bytes(), validate=True)
        dados = win32crypt.CryptUnprotectData(protegido, None, None, None, 0)[1]
        payload = json.loads(dados.decode("utf-8"))
        return payload if isinstance(payload, dict) else None
    except Exception:
        try:
            caminho.unlink()
        except Exception:
            pass
        return None


def _remover_lembranca_local() -> None:
    try:
        _arquivo_lembrar_local().unlink(missing_ok=True)
    except Exception:
        pass


def _revogar_token_emitido(token_local: dict | None) -> None:
    if not token_local:
        return
    chave = str(token_local.get("matricula") or "")
    token_id = str(token_local.get("token_id") or "")
    if not chave or not token_id:
        return
    try:
        with _LOCK:
            caminho = _arquivo_usuarios()
            with _file_lock(caminho):
                banco = _ler_banco()
                usuario = next(
                    (item for item in banco["usuarios"] if item.get("matricula") == chave),
                    None,
                )
                if not usuario:
                    return
                usuario["tokens"] = [
                    item for item in usuario.get("tokens", [])
                    if item.get("id") != token_id
                ]
                usuario["atualizado_em"] = _iso()
                _salvar_banco(banco)
    except Exception:
        pass


def _salvar_lembranca_ou_aviso(token_local: dict | None) -> tuple[bool, str]:
    if not token_local:
        encerrar_acesso_lembrado()
        return False, ""
    try:
        _proteger_local(token_local)
        return True, ""
    except Exception:
        _remover_lembranca_local()
        _revogar_token_emitido(token_local)
        return (
            False,
            "O acesso foi liberado, mas este computador nao permitiu ativar o lembrete seguro.",
        )


def _publico(usuario: dict) -> dict:
    return {
        "matricula": usuario["matricula"],
        "ultimo_checkin": usuario.get("ultimo_checkin") or "",
        "criado_em": usuario.get("criado_em") or "",
    }


def cadastrar_usuario(matricula, senha, *, lembrar=False, versao_app="") -> dict:
    chave = _matricula(matricula)
    senha_valida = _validar_senha(senha)
    with _LOCK:
        caminho = _arquivo_usuarios()
        with _file_lock(caminho):
            banco = _ler_banco()
            if any(item.get("matricula") == chave for item in banco["usuarios"]):
                raise ValueError("Esta matricula ja possui cadastro. Use a tela de login.")
            agora = _iso()
            usuario = {
                "matricula": chave,
                "senha": _novo_registro_senha(senha_valida),
                "ativo": True,
                "criado_em": agora,
                "atualizado_em": agora,
                "ultimo_login": "",
                "ultimo_checkin": "",
                "ultima_versao": str(versao_app or ""),
                "falhas_consecutivas": 0,
                "bloqueado_ate": "",
                "tokens": [],
            }
            banco["usuarios"].append(usuario)
            _salvar_banco(banco)
        return {
            "ok": True,
            "usuario": _publico(usuario),
            "lembrado": False,
            "aviso": "",
            "login_required": True,
            "msg": "Cadastro concluido. Entre com sua matricula e senha para acessar o sistema.",
        }


def _bloqueado(usuario: dict) -> bool:
    texto = str(usuario.get("bloqueado_ate") or "")
    if not texto:
        return False
    try:
        return datetime.fromisoformat(texto) > _agora()
    except Exception:
        return False


def _emitir_token(usuario: dict) -> dict:
    segredo = secrets.token_urlsafe(32)
    token_id = uuid4().hex
    agora = _iso()
    tokens = list(usuario.get("tokens") or [])[-(_MAX_TOKENS_POR_USUARIO - 1):]
    tokens.append({
        "id": token_id,
        "hash": _token_hash(segredo),
        "criado_em": agora,
        "ultimo_uso": agora,
        "computador": socket.gethostname(),
    })
    usuario["tokens"] = tokens
    return {"matricula": usuario["matricula"], "token_id": token_id, "segredo": segredo}


def autenticar_usuario(matricula, senha, *, lembrar=False, versao_app="") -> dict:
    try:
        chave = _matricula(matricula)
    except ValueError:
        chave = ""
    senha_texto = str(senha or "")
    token_local = None
    usuario = None
    with _LOCK:
        caminho = _arquivo_usuarios()
        with _file_lock(caminho):
            banco = _ler_banco()
            usuario = next(
                (item for item in banco["usuarios"] if item.get("matricula") == chave),
                None,
            )
            valido = bool(
                usuario
                and usuario.get("ativo", True)
                and not _bloqueado(usuario)
                and _senha_confere(senha_texto, usuario.get("senha") or {})
            )
            if not valido:
                if usuario and not _bloqueado(usuario):
                    falhas = int(usuario.get("falhas_consecutivas") or 0) + 1
                    usuario["falhas_consecutivas"] = falhas
                    if falhas >= _MAX_FALHAS:
                        usuario["bloqueado_ate"] = _iso(
                            _agora() + timedelta(minutes=_BLOQUEIO_MINUTOS)
                        )
                        usuario["falhas_consecutivas"] = 0
                    usuario["atualizado_em"] = _iso()
                    _salvar_banco(banco)
                raise ValueError("Matricula ou senha invalidas. Confira os dados e tente novamente.")
            agora = _iso()
            usuario["falhas_consecutivas"] = 0
            usuario["bloqueado_ate"] = ""
            usuario["ultimo_login"] = agora
            usuario["ultimo_checkin"] = agora
            usuario["ultima_versao"] = str(versao_app or "")
            usuario["atualizado_em"] = agora
            token_local = _emitir_token(usuario) if lembrar else None
            _salvar_banco(banco)
        lembrado, aviso = _salvar_lembranca_ou_aviso(token_local)
        return {
            "ok": True,
            "usuario": _publico(usuario),
            "lembrado": lembrado,
            "aviso": aviso,
        }


def verificar_acesso_lembrado(*, versao_app="") -> dict:
    local = _ler_local_protegido()
    if not local:
        return {"ok": False, "lembrado": False, "msg": "Nenhum acesso lembrado."}
    chave = str(local.get("matricula") or "")
    token_id = str(local.get("token_id") or "")
    segredo = str(local.get("segredo") or "")
    with _LOCK:
        caminho = _arquivo_usuarios()
        with _file_lock(caminho):
            banco = _ler_banco()
            usuario = next(
                (item for item in banco["usuarios"] if item.get("matricula") == chave),
                None,
            )
            token = next(
                (item for item in (usuario or {}).get("tokens", []) if item.get("id") == token_id),
                None,
            )
            valido = bool(
                usuario
                and usuario.get("ativo", True)
                and token
                and hmac.compare_digest(str(token.get("hash") or ""), _token_hash(segredo))
            )
            if not valido:
                _remover_lembranca_local()
                return {"ok": False, "lembrado": False, "msg": "O acesso lembrado expirou."}
            agora = _iso()
            token["ultimo_uso"] = agora
            usuario["ultimo_checkin"] = agora
            usuario["ultima_versao"] = str(versao_app or "")
            usuario["atualizado_em"] = agora
            _salvar_banco(banco)
            return {"ok": True, "lembrado": True, "usuario": _publico(usuario)}


def encerrar_acesso_lembrado() -> dict:
    local = _ler_local_protegido()
    _remover_lembranca_local()
    if not local:
        return {"ok": True}
    try:
        chave = str(local.get("matricula") or "")
        token_id = str(local.get("token_id") or "")
        with _LOCK:
            caminho = _arquivo_usuarios()
            with _file_lock(caminho):
                banco = _ler_banco()
                usuario = next(
                    (item for item in banco["usuarios"] if item.get("matricula") == chave),
                    None,
                )
                if usuario:
                    usuario["tokens"] = [
                        item for item in usuario.get("tokens", [])
                        if item.get("id") != token_id
                    ]
                    usuario["atualizado_em"] = _iso()
                    _salvar_banco(banco)
    except Exception:
        pass
    return {"ok": True}

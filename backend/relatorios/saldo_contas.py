from __future__ import annotations

import math
import os
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from uuid import uuid4


ABA_MODELO_SALDO_CONTAS = "SET.26"
LINHA_INICIAL_CONTAS = 20
LINHA_FINAL_CONTAS = 60
TOTAL_CONTAS_MODELO = LINHA_FINAL_CONTAS - LINHA_INICIAL_CONTAS + 1
LIMITE_TAMANHO_PLANILHA_BYTES = 50 * 1024 * 1024

# Intervalos fixos do modelo enviado: conta em A/C e saldo a direita.
# A cor amarela so vale na CELULA DO NUMERO DA CONTA. Uma coluna de saldos
# amarela (por exemplo D20:D58) nao torna as 41 aplicacoes individuais.
GRUPOS_CONTAS = (
    ("bco_movimentos", "BCO CTA MOVIMENTOS", 1, 2, 4, 17, "A3"),
    ("bco_outras", "BCO CTA MOV. OUTRAS", 3, 4, 4, 17, "C3"),
    ("centralizadora", "CENTRALIZADORA", 1, 2, 23, 28, "A22"),
    ("aplic_movimento", "APLIC. MOVIMENTO", 3, 4, 20, 60, "C19"),
)
GRUPOS_IDS = frozenset(grupo[0] for grupo in GRUPOS_CONTAS)


CONTAS_APLICACAO_MODELO = (
    "11370000",
    "11370002",
    "11370003",
    "11370004",
    "11370005",
    "11370006",
    "11370007",
    "11370008",
    "11370009",
    "11370010",
    "11370011",
    "11370012",
    "11370013",
    "11371001",
    "11371003",
    "11371065",
    "11371067",
    "11371068",
    "11371069",
    "11371070",
    "11371071",
    "11371072",
    "11371073",
    "11371074",
    "11371075",
    "11371076",
    "11371077",
    "11371078",
    "11371079",
    "11371081",
    "11371082",
    "11371083",
    "11371301",
    "11371353",
    "11371500",
    "11371601",
    "11371602",
    "11374005",
    "11374006",
    "11374007",
    "11374008",
)

_ABAS_MENSAIS_RE = re.compile(
    r"^(JAN|FEV|MAR|ABR|MAI|JUN|JUL|AGO|SET|OUT|NOV|DEZ)\.(\d{2})$",
    re.IGNORECASE,
)
_FORMULAS_ESPERADAS = {
    "D61": "=SUM(D20:D60)",
    "B59": "=D59+D60",
    "J5": "=D61-J6",
    "J6": "=B59",
    "J7": "=J3+J4+J5+J6",
}
_CENTAVOS = Decimal("0.01")


def _normalizar_texto(valor) -> str:
    return " ".join(str(valor or "").strip().upper().split())


def _normalizar_formula(valor) -> str:
    return re.sub(r"[\s$]", "", str(valor or "")).upper()


def _normalizar_conta(valor) -> str:
    if isinstance(valor, bool) or valor is None:
        return ""

    if isinstance(valor, int):
        return str(valor)

    if isinstance(valor, float):
        if not math.isfinite(valor) or not valor.is_integer():
            return ""
        return str(int(valor))

    texto = str(valor).strip()

    if re.fullmatch(r"\d+(?:\.0+)?", texto):
        return texto.split(".", 1)[0]

    return re.sub(r"\D", "", texto)


def _decimal(valor) -> Decimal | None:
    if isinstance(valor, bool) or valor is None or valor == "":
        return None

    try:
        numero = Decimal(str(valor))
    except (InvalidOperation, ValueError, TypeError):
        return None

    if not numero.is_finite():
        return None

    return numero


def _decimal_centavos(valor) -> Decimal | None:
    numero = _decimal(valor)
    return numero.quantize(_CENTAVOS, rounding=ROUND_HALF_UP) if numero is not None else None


def _numero_front(valor: Decimal | None):
    return float(valor) if valor is not None else None


def _valor_formula_constante(valor) -> Decimal | None:
    correspondencia = re.fullmatch(r"=\s*(-?\d+(?:[.,]\d+)?)\s*", str(valor or ""))

    if not correspondencia:
        return None

    return _decimal_centavos(correspondencia.group(1).replace(",", "."))


def _periodo_planilha(valor):
    if isinstance(valor, datetime):
        referencia = valor.date()
    elif isinstance(valor, date):
        referencia = valor
    else:
        referencia = None

    if referencia is None:
        return {"data": "", "mes": "", "ano": ""}

    return {
        "data": referencia.isoformat(),
        "mes": f"{referencia.month:02d}",
        "ano": str(referencia.year),
    }


def _validar_arquivo(caminho) -> Path:
    texto = str(caminho or "").strip()

    if not texto:
        raise ValueError("Selecione uma planilha Excel para continuar.")

    arquivo = Path(texto).expanduser()

    if not arquivo.exists() or not arquivo.is_file():
        raise ValueError("A planilha selecionada nao foi encontrada.")

    if arquivo.suffix.lower() != ".xlsx":
        raise ValueError("Selecione uma planilha no formato .xlsx.")

    if arquivo.stat().st_size > LIMITE_TAMANHO_PLANILHA_BYTES:
        raise ValueError("A planilha ultrapassa o limite de 50 MB.")

    return arquivo.resolve()


def _carregar_planilha(caminho: Path, *, data_only: bool):
    try:
        from openpyxl import load_workbook
    except ImportError as exc:
        raise RuntimeError(
            "O componente de leitura de planilhas nao esta instalado nesta versao do aplicativo."
        ) from exc

    return load_workbook(
        filename=caminho,
        read_only=True,
        data_only=data_only,
        keep_links=False,
    )


def _carregar_planilha_edicao(caminho: Path):
    try:
        from openpyxl import load_workbook
    except ImportError as exc:
        raise RuntimeError(
            "O componente de escrita de planilhas nao esta instalado nesta versao do aplicativo."
        ) from exc

    return load_workbook(
        filename=caminho,
        data_only=False,
        keep_links=True,
    )


def _proximo_caminho_saida(arquivo: Path) -> Path:
    candidato = arquivo.with_name(f"{arquivo.stem} - PREENCHIDO{arquivo.suffix}")

    if not candidato.exists():
        return candidato

    indice = 2

    while True:
        candidato = arquivo.with_name(
            f"{arquivo.stem} - PREENCHIDO ({indice}){arquivo.suffix}"
        )

        if not candidato.exists():
            return candidato

        indice += 1



def _conta_amarela(celula) -> bool:
    """Somente destaque amarelo do identificador contabil determina consulta solo."""
    try:
        preenchimento = celula.fill
        if preenchimento.patternType != "solid":
            return False
        cor = preenchimento.fgColor
        if cor.type == "rgb" and isinstance(cor.rgb, str):
            return cor.rgb[-6:].upper() in {"FFFF00", "FFF200", "FFEB00"}
        if cor.type == "indexed":
            return cor.indexed in {5, 6, 13}
    except (AttributeError, TypeError):
        return False
    return False


def ler_grupos_contas_modelo(sheet) -> list[dict]:
    """Identifica contas e destinos no modelo, sem incluir subtotais/intervalos."""
    grupos = []
    contas_ja_vistas = set()
    for chave, nome, coluna_conta, coluna_valor, inicio, fim, cabecalho in GRUPOS_CONTAS:
        titulo = _normalizar_texto(sheet[cabecalho].value)
        if titulo != _normalizar_texto(nome):
            # Um modelo incompleto nao pode causar lancamentos em locais errados.
            if chave == "aplic_movimento":
                raise ValueError("Cabecalho APLIC. MOVIMENTO nao encontrado em C19.")
            continue
        itens = []
        for linha in range(inicio, fim + 1):
            celula_conta = sheet.cell(linha, coluna_conta)
            conta = _normalizar_conta(celula_conta.value)
            if not re.fullmatch(r"\d{8}", conta):
                continue
            if conta in contas_ja_vistas:
                raise ValueError(f"Conta duplicada em mais de um grupo: {conta}.")
            contas_ja_vistas.add(conta)
            itens.append({
                "conta": conta,
                "celula": f"{chr(64 + coluna_valor)}{linha}",
                "individual": _conta_amarela(celula_conta),
            })
        if itens:
            grupos.append({
                "id": chave,
                "nome": nome,
                "quantidade": len(itens),
                "individuais": sum(item["individual"] for item in itens),
                "em_lote": sum(not item["individual"] for item in itens),
                "contas": itens,
            })
    return grupos


def _validar_grupos_selecionados(grupos_disponiveis, selecionados):
    if selecionados is None:
        selecionados = ("aplic_movimento",)  # compatibilidade com fluxo anterior
    if not isinstance(selecionados, (list, tuple, set, frozenset)):
        raise ValueError("A selecao de grupos deve ser uma lista.")
    ids = list(selecionados)
    if not ids or any(not isinstance(chave, str) for chave in ids):
        raise ValueError("Selecione pelo menos um grupo de contas.")
    if len(set(ids)) != len(ids):
        raise ValueError("Ha grupos repetidos na selecao.")
    disponiveis = {item["id"]: item for item in grupos_disponiveis}
    desconhecidos = set(ids) - disponiveis.keys()
    if desconhecidos:
        raise ValueError(f"Grupo(s) indisponivel(is) nesta planilha: {', '.join(sorted(desconhecidos))}.")
    return [disponiveis[chave] for chave in ids]


def planejar_consultas_saldo_contas(analise, selecionados=None) -> dict:
    """Agrupa contas normais e separa cada conta destacada em amarelo."""
    grupos = _validar_grupos_selecionados(analise.get("grupos") or [], selecionados)
    lotes = []
    destinos = {}
    for grupo in grupos:
        contas_lote = []
        for item in grupo["contas"]:
            destinos[item["conta"]] = item["celula"]
            if not item["individual"]:
                contas_lote.append(item["conta"])
        if contas_lote:
            lotes.append({"grupo": grupo["nome"], "contas": contas_lote, "individual": False})
        for item in grupo["contas"]:
            if item["individual"]:
                lotes.append({"grupo": grupo["nome"], "contas": [item["conta"]], "individual": True})
    return {"grupos": [g["id"] for g in grupos], "lotes": lotes, "destinos": destinos}


def preencher_planilha_saldo_contas(
    caminho,
    saldos_por_conta,
    mes,
    ano,
    aba_modelo: str = ABA_MODELO_SALDO_CONTAS,
    grupos_selecionados=None,
) -> dict:
    arquivo = _validar_arquivo(caminho)
    analise_origem = analisar_planilha_saldo_contas(arquivo, aba_modelo=aba_modelo)

    if not analise_origem.get("ok") or not analise_origem.get("modelo_valido"):
        raise ValueError(
            "A planilha selecionada nao corresponde ao modelo SET.26 validado."
        )

    try:
        mes_numero = int(mes)
        ano_numero = int(ano)
    except (TypeError, ValueError) as exc:
        raise ValueError("Informe mes e ano validos para preencher a planilha.") from exc

    if mes_numero < 1 or mes_numero > 12:
        raise ValueError("O mes do relatorio deve estar entre 1 e 12.")

    if ano_numero < 2020 or ano_numero > 2099:
        raise ValueError("O ano do relatorio deve estar entre 2020 e 2099.")

    plano = planejar_consultas_saldo_contas(analise_origem, grupos_selecionados)
    destinos = plano["destinos"]
    saldos_normalizados = {}
    for conta_bruta, saldo_bruto in dict(saldos_por_conta or {}).items():
        conta = _normalizar_conta(conta_bruta)
        if conta not in destinos:
            continue
        saldo = _decimal_centavos(saldo_bruto)
        if saldo is None:
            raise ValueError(f"Saldo invalido retornado para a conta {conta}.")
        saldos_normalizados[conta] = saldo

    contas_ausentes = sorted(destinos.keys() - saldos_normalizados.keys())
    if contas_ausentes:
        raise ValueError(
            f"O SAP retornou apenas {len(saldos_normalizados)} de "
            f"{len(destinos)} contas selecionadas. Planilha nao modificada. "
            f"Faltantes: {', '.join(contas_ausentes)}."
        )

    workbook = None
    caminho_saida = _proximo_caminho_saida(arquivo)
    caminho_temporario = caminho_saida.with_name(
        f".{caminho_saida.stem}.{uuid4().hex}.tmp.xlsx"
    )

    try:
        workbook = _carregar_planilha_edicao(arquivo)
        sheet = workbook[aba_modelo]
        total = Decimal("0")

        for conta, referencia in destinos.items():
            saldo = saldos_normalizados[conta]
            sheet[referencia].value = float(saldo)
            total += saldo

        total = total.quantize(_CENTAVOS, rounding=ROUND_HALF_UP)
        if "aplic_movimento" in plano["grupos"]:
            # A celula C61 sempre foi o subtotal independente das 41 aplicacoes.
            # Nao alteramos essa celula se o usuario consultou apenas outros grupos.
            total_aplic = sum(
                (saldos_normalizados[item["conta"]]
                 for grupo in analise_origem["grupos"]
                 if grupo["id"] == "aplic_movimento"
                 for item in grupo["contas"]),
                Decimal("0"),
            )
            sheet["C61"] = float(total_aplic.quantize(_CENTAVOS))
        sheet["C1"] = date(ano_numero, mes_numero, 1)

        calculo = getattr(workbook, "calculation", None)

        if calculo is not None:
            calculo.fullCalcOnLoad = True
            calculo.forceFullCalc = True
            calculo.calcMode = "auto"

        workbook.save(caminho_temporario)
        workbook.close()
        workbook = None
        os.replace(caminho_temporario, caminho_saida)
    finally:
        if workbook is not None:
            workbook.close()

        if caminho_temporario.exists():
            try:
                caminho_temporario.unlink()
            except OSError:
                pass

    return {
        "ok": True,
        "msg": "Planilha preenchida e salva com sucesso.",
        "caminho_origem": str(arquivo),
        "caminho_saida": str(caminho_saida),
        "arquivo_saida": caminho_saida.name,
        "aba_modelo": aba_modelo,
        "periodo": {
            "mes": f"{mes_numero:02d}",
            "ano": str(ano_numero),
        },
        "grupos_selecionados": plano["grupos"],
        "contas_preenchidas": len(destinos),
        "contas_retornadas": len(saldos_normalizados),
        "contas_zeradas": sum(valor == 0 for valor in saldos_normalizados.values()),
        "total": _numero_front(total),
    }


def _criar_verificacao(codigo: str, titulo: str, ok: bool, detalhe: str) -> dict:
    return {
        "codigo": codigo,
        "titulo": titulo,
        "ok": bool(ok),
        "detalhe": detalhe,
    }


def analisar_planilha_saldo_contas(
    caminho,
    aba_modelo: str = ABA_MODELO_SALDO_CONTAS,
) -> dict:
    arquivo = _validar_arquivo(caminho)
    workbook_formulas = None
    workbook_valores = None

    try:
        workbook_formulas = _carregar_planilha(arquivo, data_only=False)
        workbook_valores = _carregar_planilha(arquivo, data_only=True)

        abas = list(workbook_formulas.sheetnames)

        if aba_modelo not in workbook_formulas.sheetnames:
            return {
                "ok": False,
                "msg": f'A aba-modelo "{aba_modelo}" nao foi encontrada na planilha.',
                "caminho": str(arquivo),
                "arquivo": arquivo.name,
                "abas": abas,
            }

        sheet_formulas = workbook_formulas[aba_modelo]
        sheet_valores = workbook_valores[aba_modelo]
        cabecalho_contas = _normalizar_texto(sheet_formulas["C19"].value)
        cabecalho_valores = _normalizar_texto(sheet_formulas["D19"].value)
        periodo = _periodo_planilha(sheet_formulas["C1"].value)
        contas = []
        saldos_decimais = []
        contas_normalizadas = []

        for linha in range(LINHA_INICIAL_CONTAS, LINHA_FINAL_CONTAS + 1):
            conta = _normalizar_conta(sheet_formulas.cell(linha, 3).value)
            saldo = _decimal_centavos(sheet_valores.cell(linha, 4).value)
            contas_normalizadas.append(conta)
            saldos_decimais.append(saldo)
            contas.append(
                {
                    "linha": linha,
                    "conta": conta,
                    "saldo_atual": _numero_front(saldo),
                    "status": "vazio" if saldo is None else ("zero" if saldo == 0 else "preenchido"),
                }
            )

        grupos_disponiveis = ler_grupos_contas_modelo(sheet_formulas)
        contas_esperadas = list(CONTAS_APLICACAO_MODELO)
        contas_encontradas = [conta for conta in contas_normalizadas if conta]
        contas_ausentes = [conta for conta in contas_esperadas if conta not in contas_encontradas]
        contas_inesperadas = [conta for conta in contas_encontradas if conta not in contas_esperadas]
        contas_duplicadas = sorted(
            {conta for conta in contas_encontradas if contas_encontradas.count(conta) > 1}
        )
        lista_contas_ok = contas_normalizadas == contas_esperadas
        verificacoes = [
            _criar_verificacao(
                "aba_modelo",
                "Aba-modelo",
                True,
                f"{aba_modelo} localizada.",
            ),
            _criar_verificacao(
                "cabecalhos",
                "Cabecalhos do bloco",
                cabecalho_contas == "APLIC. MOVIMENTO" and cabecalho_valores == "INDIVIDUAIS",
                f"C19: {cabecalho_contas or '--'} | D19: {cabecalho_valores or '--'}.",
            ),
            _criar_verificacao(
                "lista_contas",
                "Lista de contas",
                lista_contas_ok and not contas_duplicadas,
                f"{len(contas_encontradas)} de {TOTAL_CONTAS_MODELO} contas do modelo.",
            ),
        ]

        formulas_ok = True

        for celula, formula_esperada in _FORMULAS_ESPERADAS.items():
            formula_atual = sheet_formulas[celula].value
            formula_ok = _normalizar_formula(formula_atual) == _normalizar_formula(formula_esperada)
            formulas_ok = formulas_ok and formula_ok
            verificacoes.append(
                _criar_verificacao(
                    f"formula_{celula.lower()}",
                    f"Formula {celula}",
                    formula_ok,
                    formula_atual or "Formula ausente.",
                )
            )

        total_detalhes = sum((saldo or Decimal("0")) for saldo in saldos_decimais).quantize(
            _CENTAVOS,
            rounding=ROUND_HALF_UP,
        )
        total_azul = _decimal_centavos(sheet_valores["C61"].value)

        if total_azul is None:
            total_azul = _decimal_centavos(sheet_formulas["C61"].value)

        if total_azul is None:
            total_azul = _valor_formula_constante(sheet_formulas["C61"].value)

        total_verde_cache = _decimal_centavos(sheet_valores["D61"].value)
        total_verde = total_verde_cache if total_verde_cache is not None else total_detalhes
        diferenca = None
        conferencia = "pendente"

        if total_azul is not None and total_verde is not None:
            diferenca = (total_azul - total_verde).quantize(_CENTAVOS, rounding=ROUND_HALF_UP)
            conferencia = "ok" if diferenca == 0 else "divergente"

        saldos_preenchidos = sum(1 for saldo in saldos_decimais if saldo is not None)
        saldos_zerados = sum(1 for saldo in saldos_decimais if saldo == 0)
        abas_mensais = [aba for aba in abas if _ABAS_MENSAIS_RE.fullmatch(aba)]
        avisos = []

        if contas_ausentes:
            avisos.append(f"Contas ausentes: {', '.join(contas_ausentes)}.")

        if contas_inesperadas:
            avisos.append(f"Contas diferentes do modelo: {', '.join(contas_inesperadas)}.")

        if contas_duplicadas:
            avisos.append(f"Contas duplicadas: {', '.join(contas_duplicadas)}.")

        if saldos_preenchidos < TOTAL_CONTAS_MODELO:
            avisos.append(
                f"A aba possui {TOTAL_CONTAS_MODELO - saldos_preenchidos} saldo(s) vazio(s). "
                "Vazio nao deve ser tratado como saldo zero."
            )

        if conferencia == "divergente":
            avisos.append("O total azul e a soma verde apresentam diferenca.")

        if not formulas_ok:
            avisos.append("Uma ou mais formulas de conferencia diferem do modelo SET.26.")

        modelo_valido = (
            cabecalho_contas == "APLIC. MOVIMENTO"
            and cabecalho_valores == "INDIVIDUAIS"
            and lista_contas_ok
            and not contas_duplicadas
            and formulas_ok
        )

        return {
            "ok": True,
            "msg": "Planilha analisada com sucesso.",
            "caminho": str(arquivo),
            "arquivo": arquivo.name,
            "abas": abas,
            "abas_mensais": abas_mensais,
            "aba_modelo": aba_modelo,
            "periodo": periodo,
            "modelo_valido": modelo_valido,
            "pronto_para_automacao": modelo_valido,
            "integracao_sap_disponivel": True,
            "bloco": {
                "cabecalho": "C19:D19",
                "contas": "C20:C60",
                "valores": "D20:D60",
                "total_independente": "C61",
                "total_formula": "D61",
            },
            "resumo": {
                "contas_esperadas": TOTAL_CONTAS_MODELO,
                "contas_encontradas": len(contas_encontradas),
                "saldos_preenchidos": saldos_preenchidos,
                "saldos_vazios": TOTAL_CONTAS_MODELO - saldos_preenchidos,
                "saldos_zerados": saldos_zerados,
                "total_detalhes": _numero_front(total_detalhes),
                "total_azul": _numero_front(total_azul),
                "total_verde": _numero_front(total_verde),
                "diferenca": _numero_front(diferenca),
                "conferencia": conferencia,
            },
            "contas": contas,
            "grupos": grupos_disponiveis,
            "verificacoes": verificacoes,
            "avisos": avisos,
        }
    finally:
        if workbook_formulas is not None:
            workbook_formulas.close()

        if workbook_valores is not None:
            workbook_valores.close()

"""
CLI de consulta ao banco de dados IBS/CBS
==========================================
Uso:
  python consultar.py stats              — estatísticas gerais
  python consultar.py listar             — últimas 20 normas
  python consultar.py listar --alto      — apenas normas de impacto alto
  python consultar.py buscar TERMO       — busca por texto livre
  python consultar.py historico          — histórico de execuções
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from database import Database


def _sep():
    print("─" * 72)


def cmd_stats(db: Database, _args) -> None:
    s = db.estatisticas()
    _sep()
    print("  ESTATÍSTICAS — Monitor IBS/CBS")
    _sep()
    print(f"  Total de normas indexadas : {s['total_normas']}")
    print(f"  Impacto alto              : {s['impacto_alto']}")
    print(f"  Impacto médio             : {s['impacto_medio']}")
    print(f"  Impacto baixo             : {s['impacto_baixo']}")
    print(f"  Última norma registrada   : {s['ultima_norma_em'] or '—'}")
    print(f"  Total de execuções        : {s['total_execucoes']}")
    print(f"  Última execução           : {s['ultima_execucao_em'] or '—'}")
    _sep()


def _print_norma(n: dict, idx: int) -> None:
    print(f"\n  [{idx}] {n['titulo'][:90]}")
    print(f"       Tipo: {n.get('tipo','—')} | Número: {n.get('numero','—')} "
          f"| Data: {n.get('data_pub','—')}")
    print(f"       Impacto: {n.get('impacto','—').upper()} "
          f"| Score: {n.get('score','—')}/10 | Fonte: {n.get('fonte','—')[:40]}")
    ementa = n.get("ementa", "")
    if ementa:
        print(f"       Ementa: {ementa[:120]}")
    pontos = n.get("pontos", []) or []
    for i, p in enumerate(pontos[:3], 1):
        print(f"         • {p[:100]}")
    url = n.get("url", "")
    if url:
        print(f"       URL: {url[:90]}")


def cmd_listar(db: Database, args) -> None:
    impacto = "alto" if getattr(args, "alto", False) else (
              "medio" if getattr(args, "medio", False) else None)
    normas = db.listar_normas(impacto=impacto, limit=20)
    _sep()
    print(f"  NORMAS INDEXADAS{' — Impacto: ' + impacto.upper() if impacto else ''} ({len(normas)} registros)")
    _sep()
    if not normas:
        print("  Nenhuma norma encontrada.")
    for i, n in enumerate(normas, 1):
        _print_norma(n, i)
    _sep()


def cmd_buscar(db: Database, args) -> None:
    termo = " ".join(args.termo)
    normas = db.buscar_por_texto(termo)
    _sep()
    print(f"  BUSCA: '{termo}' — {len(normas)} resultado(s)")
    _sep()
    if not normas:
        print("  Nenhuma norma encontrada.")
    for i, n in enumerate(normas, 1):
        _print_norma(n, i)
    _sep()


def cmd_historico(db: Database, _args) -> None:
    hist = db.historico_execucoes(limit=15)
    _sep()
    print("  HISTÓRICO DE EXECUÇÕES (últimas 15)")
    _sep()
    for h in hist:
        email = "✓" if h["email_enviado"] else "✗"
        print(f"  {h['executado_em']}  |  "
              f"Normas: {h['normas_encontradas']:>3}  |  "
              f"E-mail: {email}  |  "
              f"{h.get('observacoes','') or ''}")
    _sep()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Consulta o banco de dados local do Monitor IBS/CBS"
    )
    sub = parser.add_subparsers(dest="cmd")

    sub.add_parser("stats", help="Estatísticas gerais")

    p_list = sub.add_parser("listar", help="Lista normas indexadas")
    p_list.add_argument("--alto",  action="store_true", help="Apenas impacto alto")
    p_list.add_argument("--medio", action="store_true", help="Apenas impacto médio")

    p_busca = sub.add_parser("buscar", help="Busca por texto")
    p_busca.add_argument("termo", nargs="+", help="Termo de busca")

    sub.add_parser("historico", help="Histórico de execuções")

    args = parser.parse_args()
    if not args.cmd:
        parser.print_help()
        return

    with Database() as db:
        cmds = {
            "stats": cmd_stats,
            "listar": cmd_listar,
            "buscar": cmd_buscar,
            "historico": cmd_historico,
        }
        cmds[args.cmd](db, args)


if __name__ == "__main__":
    main()

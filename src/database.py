"""
Módulo de persistência SQLite — Monitor IBS/CBS
================================================
Gerencia as tabelas:
  - normas       : registro de todas as normas encontradas
  - execucoes    : log de cada ciclo de monitoramento
"""

import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent.parent / "data" / "ibs_cbs.db"


class Database:
    """Wrapper de acesso ao banco SQLite com criação automática de schema."""

    def __init__(self, db_path: Path = DB_PATH) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.path = db_path
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._criar_schema()
        log.info("Banco de dados: %s", db_path)

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    def _criar_schema(self) -> None:
        self._conn.executescript("""
        PRAGMA journal_mode=WAL;
        PRAGMA foreign_keys=ON;

        CREATE TABLE IF NOT EXISTS normas (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            hash_id        TEXT    NOT NULL UNIQUE,
            titulo         TEXT    NOT NULL,
            url            TEXT,
            fonte          TEXT,
            tipo           TEXT,               -- lei_complementar, decreto, resolucao...
            numero         TEXT,               -- ex: "214/2025"
            data_pub       TEXT,               -- DD/MM/AAAA
            ementa         TEXT,
            pontos         TEXT,               -- JSON array
            tributos       TEXT,               -- JSON array: ["IBS","CBS"]
            impacto        TEXT DEFAULT 'baixo', -- alto|medio|baixo
            urgencia       TEXT DEFAULT 'medio_prazo',
            score          INTEGER DEFAULT 1,  -- 1-10
            observacoes    TEXT,
            criado_em      TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            atualizado_em  TEXT NOT NULL DEFAULT (datetime('now','localtime'))
        );

        CREATE INDEX IF NOT EXISTS idx_normas_hash   ON normas(hash_id);
        CREATE INDEX IF NOT EXISTS idx_normas_tipo   ON normas(tipo);
        CREATE INDEX IF NOT EXISTS idx_normas_impacto ON normas(impacto);
        CREATE INDEX IF NOT EXISTS idx_normas_data   ON normas(data_pub);

        CREATE TABLE IF NOT EXISTS execucoes (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            executado_em        TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            normas_encontradas  INTEGER DEFAULT 0,
            email_enviado       INTEGER DEFAULT 0,  -- 0=não, 1=sim
            observacoes         TEXT
        );
        """)
        self._conn.commit()

    # ------------------------------------------------------------------
    # Operações — Normas
    # ------------------------------------------------------------------

    def norma_existe(self, hash_id: str) -> bool:
        cur = self._conn.execute(
            "SELECT 1 FROM normas WHERE hash_id = ?", (hash_id,)
        )
        return cur.fetchone() is not None

    def inserir_norma(
        self,
        hash_id: str,
        titulo: str,
        url: Optional[str],
        fonte: Optional[str],
        tipo: Optional[str],
        numero: Optional[str],
        data_pub: Optional[str],
        ementa: Optional[str],
        pontos: Optional[str],      # JSON string
        tributos: Optional[str],    # JSON string
        impacto: str = "baixo",
        urgencia: str = "medio_prazo",
        score: int = 1,
        observacoes: Optional[str] = None,
    ) -> int:
        agora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cur = self._conn.execute(
            """
            INSERT INTO normas
                (hash_id, titulo, url, fonte, tipo, numero, data_pub,
                 ementa, pontos, tributos, impacto, urgencia, score,
                 observacoes, criado_em, atualizado_em)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(hash_id) DO UPDATE SET
                titulo        = excluded.titulo,
                ementa        = excluded.ementa,
                pontos        = excluded.pontos,
                tributos      = excluded.tributos,
                impacto       = excluded.impacto,
                urgencia      = excluded.urgencia,
                score         = excluded.score,
                atualizado_em = excluded.atualizado_em
            """,
            (hash_id, titulo, url, fonte, tipo, numero, data_pub,
             ementa, pontos, tributos, impacto, urgencia, score,
             observacoes, agora, agora),
        )
        self._conn.commit()
        return cur.lastrowid

    def listar_normas(
        self,
        impacto: Optional[str] = None,
        tipo: Optional[str] = None,
        tributo: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict]:
        """
        Lista normas com filtros opcionais.
        tributo: filtra registros que contenham o termo no campo tributos (JSON).
        """
        where, params = [], []
        if impacto:
            where.append("impacto = ?")
            params.append(impacto)
        if tipo:
            where.append("tipo = ?")
            params.append(tipo)
        if tributo:
            where.append("tributos LIKE ?")
            params.append(f'%"{tributo}"%')

        clause = ("WHERE " + " AND ".join(where)) if where else ""
        params += [limit, offset]
        rows = self._conn.execute(
            f"""
            SELECT * FROM normas
            {clause}
            ORDER BY criado_em DESC
            LIMIT ? OFFSET ?
            """,
            params,
        ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def buscar_por_texto(self, termo: str, limit: int = 20) -> list[dict]:
        """Busca full-text simples por título ou ementa."""
        like = f"%{termo}%"
        rows = self._conn.execute(
            """
            SELECT * FROM normas
            WHERE titulo LIKE ? OR ementa LIKE ? OR pontos LIKE ?
            ORDER BY score DESC, criado_em DESC
            LIMIT ?
            """,
            (like, like, like, limit),
        ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def estatisticas(self) -> dict:
        """Retorna estatísticas gerais do banco."""
        cur = self._conn.execute(
            """
            SELECT
                COUNT(*)                                    AS total,
                SUM(CASE WHEN impacto='alto'  THEN 1 ELSE 0 END) AS alto,
                SUM(CASE WHEN impacto='medio' THEN 1 ELSE 0 END) AS medio,
                SUM(CASE WHEN impacto='baixo' THEN 1 ELSE 0 END) AS baixo,
                MAX(criado_em)                              AS ultima_norma
            FROM normas
            """
        )
        row = cur.fetchone()
        exec_cur = self._conn.execute(
            "SELECT COUNT(*), MAX(executado_em) FROM execucoes"
        )
        exec_row = exec_cur.fetchone()
        return {
            "total_normas": row["total"] or 0,
            "impacto_alto": row["alto"] or 0,
            "impacto_medio": row["medio"] or 0,
            "impacto_baixo": row["baixo"] or 0,
            "ultima_norma_em": row["ultima_norma"],
            "total_execucoes": exec_row[0] or 0,
            "ultima_execucao_em": exec_row[1],
        }

    # ------------------------------------------------------------------
    # Operações — Execuções
    # ------------------------------------------------------------------

    def registrar_execucao(
        self,
        normas_encontradas: int = 0,
        email_enviado: bool = False,
        observacoes: Optional[str] = None,
    ) -> None:
        self._conn.execute(
            """
            INSERT INTO execucoes (normas_encontradas, email_enviado, observacoes)
            VALUES (?, ?, ?)
            """,
            (normas_encontradas, int(email_enviado), observacoes),
        )
        self._conn.commit()

    def historico_execucoes(self, limit: int = 30) -> list[dict]:
        rows = self._conn.execute(
            """
            SELECT * FROM execucoes
            ORDER BY executado_em DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Utilitários
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict:
        d = dict(row)
        for campo in ("pontos", "tributos"):
            if d.get(campo):
                try:
                    d[campo] = json.loads(d[campo])
                except (json.JSONDecodeError, TypeError):
                    d[campo] = []
        return d

    def fechar(self) -> None:
        self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.fechar()

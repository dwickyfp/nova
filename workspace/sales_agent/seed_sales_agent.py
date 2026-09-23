# ruff: noqa: E501 -- Agent instructions are stored as readable, sentence-per-line prose.

"""Create or update the Sales Agent semantic model and Agent Studio record.

Run from ``backend/`` with the StarRocks host/port supplied through Nova's normal
configuration environment. The operation is idempotent by owner and object name.
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
from typing import Any

from app.core.database import db
from app.modules.agents.instructions import compile_agent_instructions
from app.modules.agents.repository import agent_repository
from app.modules.agents.semantic.compiler import SemanticCompiler
from app.modules.agents.semantic.ir import SemanticModelIR
from app.modules.agents.semantic.ossie import parse_ossie
from app.modules.agents.semantic.planning import SemanticPlan
from app.modules.agents.semantic.runtime import validate_semantic_model_ir

ROOT = Path(__file__).resolve().parent
OSSIE_PATH = ROOT / "nova_sales_360.ossie.yaml"
MODEL_NAME = "nova_sales_360"
AGENT_NAME = "Sales Agent"

DESCRIPTION = (
    "Commercial analytics copilot for NOVA_SALES: revenue, margin, customers, "
    "products, channels, campaigns, stores, returns, and sales-team quota performance."
)

RESPONSE_INSTRUCTIONS = """
Jawab dalam bahasa yang dipakai pengguna. Mulai dengan jawaban eksekutif satu atau dua kalimat yang langsung menyebut kesimpulan utama.
Untuk jawaban numerik, selalu tampilkan periode, filter, definisi KPI, dan satuan; semua nilai uang menggunakan IDR dan format Rp.
Gunakan tabel ringkas untuk perbandingan kategori, wilayah, toko, produk, customer tier, atau sales representative.
Untuk tren, sertakan nilai periode, perubahan absolut, dan perubahan persentase bila pembanding tersedia.
Pisahkan fakta hasil query dari interpretasi bisnis. Tandai interpretasi sebagai indikasi, bukan sebab pasti.
Sebutkan ukuran populasi atau jumlah order saat menilai rate agar persentase kecil tidak menyesatkan.
Jika hasil kosong, katakan bahwa tidak ada baris yang cocok dan ulangi filter yang dipakai.
Jika pertanyaan ambigu, ajukan satu pertanyaan klarifikasi yang paling menentukan. Bila tetap dapat dijawab, gunakan recognized revenue sebagai arti default dari sales dan nyatakan asumsi itu.
Soroti return rate di atas 6%, gross margin di bawah 20%, cancellation rate di atas 3%, atau quota attainment di bawah 90% sebagai area perhatian, bukan otomatis sebagai masalah kausal.
Jangan mengarang angka, nama customer, alasan perubahan, atau rekomendasi yang tidak ditopang hasil query.
Jangan tampilkan email customer kecuali pengguna secara eksplisit meminta record-level investigation yang sah; utamakan customer_code.
Tutup jawaban analitis dengan satu next step yang spesifik hanya jika next step tersebut benar-benar membantu keputusan.
""".strip()

ORCHESTRATION_INSTRUCTIONS = """
Gunakan semantic_query terlebih dahulu untuk KPI dan pertanyaan bisnis yang tercakup semantic model nova_sales_360.
Gunakan query_execute hanya untuk inspeksi schema, validasi detail, rekonsiliasi, atau pertanyaan read-only yang belum dapat diekspresikan oleh semantic query.
Gunakan data_to_chart untuk tren waktu atau perbandingan kategori ketika visual lebih jelas; batasi chart utama pada paling banyak 12 seri atau kategori.
Gunakan order_date untuk metrik transaksi dan sales_month untuk metrik target atau quota.
Pastikan perbandingan periode memiliki panjang periode yang setara dan jangan membandingkan bulan berjalan parsial dengan bulan penuh tanpa menyebutkannya.
Gunakan recognized_revenue untuk penjualan yang diakui; metrik ini mengecualikan Cancelled. Jangan menjumlahkan persentase gross margin, return rate, discount rate, average order value, atau quota attainment.
Untuk analisis target, gunakan dataset rep_performance dan metrik actual_revenue, target_revenue, serta quota_attainment. Jangan menggabungkan target bulanan ke fact_sales pada grain transaksi.
Untuk ranking tanpa jumlah yang diminta, gunakan top 10 dan urutkan berdasarkan metrik utama secara menurun.
Untuk diagnosis penurunan revenue, periksa setidaknya order_count dan average_order_value sebelum memberi interpretasi.
Untuk evaluasi promo, periksa recognized_revenue, discount_rate, gross_margin_pct, dan return_rate bersama-sama.
Untuk customer-level analysis, gunakan customer_code sebagai identifier tampilan dan batasi hasil secara wajar.
Jika tool tidak dapat diakses atau semantic plan ambigu, jelaskan keterbatasannya dan minta klarifikasi; jangan mengganti hasil dengan perkiraan.
""".strip()

SAMPLE_QUESTIONS = [
    "Bandingkan recognized revenue, gross margin, dan order count per channel untuk 2025.",
    "Wilayah mana yang revenue-nya turun dibanding bulan sebelumnya, dan apakah karena order count atau AOV?",
    "Tampilkan 10 sales representative dengan quota attainment tertinggi pada bulan penuh terbaru.",
    "Customer tier mana yang memiliki return rate tertinggi dan gross margin terendah?",
    "Bagaimana performa kategori produk selama campaign Ramadan dibanding periode non-campaign?",
    "Toko mana yang revenue-nya tinggi tetapi return rate-nya di atas 6%?",
    "Buat chart tren bulanan Mobile App versus Store sejak 2023.",
    "Siapa customer bernilai tinggi yang terakhir bertransaksi lebih dari 90 hari lalu?",
]

VERIFIED_PLANS: list[dict[str, Any]] = [
    {
        "question": "Show recognized revenue by sales channel.",
        "plan": {
            "metrics": ["recognized_revenue"],
            "dimensions": ["sales_channel"],
            "order_by": [{"field": "recognized_revenue", "direction": "desc"}],
        },
        "signature": "sales_channel,recognized_revenue",
        "tags": ["revenue", "channel"],
    },
    {
        "question": "Chart monthly recognized revenue.",
        "plan": {
            "metrics": ["recognized_revenue"],
            "time": {"dimension": "order_date", "grain": "month"},
        },
        "signature": "order_date,recognized_revenue",
        "tags": ["revenue", "trend"],
    },
    {
        "question": "Who are the top 10 sales representatives by recognized revenue?",
        "plan": {
            "metrics": ["recognized_revenue"],
            "dimensions": ["sales_rep_name"],
            "order_by": [{"field": "recognized_revenue", "direction": "desc"}],
            "limit": 10,
        },
        "signature": "sales_rep_name,recognized_revenue",
        "tags": ["revenue", "sales-rep", "ranking"],
    },
    {
        "question": "Show quota attainment by sales representative.",
        "plan": {
            "metrics": ["quota_attainment"],
            "dimensions": ["performance_sales_rep_name"],
            "order_by": [{"field": "quota_attainment", "direction": "desc"}],
            "limit": 10,
        },
        "signature": "performance_sales_rep_name,quota_attainment",
        "tags": ["quota", "sales-rep"],
    },
    {
        "question": "Compare gross margin by product category.",
        "plan": {
            "metrics": ["gross_margin_pct"],
            "dimensions": ["category"],
            "order_by": [{"field": "gross_margin_pct", "direction": "desc"}],
        },
        "signature": "category,gross_margin_pct",
        "tags": ["margin", "product"],
    },
]


async def upsert_model(owner: str, definition: dict[str, Any], version: str) -> dict:
    fields = {
        "name": MODEL_NAME,
        "description": definition.get("description", ""),
        "database_name": "NOVA_SALES",
        "schema_name": None,
        "ossie_version": version,
        "definition": definition,
        "source_file_id": None,
    }
    matches = [
        row
        for row in await agent_repository.list_semantic_models(owner_name=owner)
        if row["name"] == MODEL_NAME
    ]
    if matches:
        model = await agent_repository.update_semantic_model(
            matches[0]["semantic_model_id"], owner_name=owner, fields=fields
        )
        assert model is not None
        return model
    return await agent_repository.create_semantic_model(owner_name=owner, fields=fields)


async def upsert_agent(owner: str, model_id: str) -> dict:
    compiled = compile_agent_instructions(
        response=RESPONSE_INSTRUCTIONS,
        orchestration=ORCHESTRATION_INSTRUCTIONS,
        description=DESCRIPTION,
        response_style="tabular",
    ).as_dict()
    fields = {
        "name": AGENT_NAME,
        "description": DESCRIPTION,
        "database_name": "NOVA_SALES",
        "schema_name": None,
        "avatar": "chart-no-axes-combined",
        "color": "#2563EB",
        "model_provider_id": None,
        "model_name": None,
        "instructions_response": RESPONSE_INSTRUCTIONS,
        "instructions_orchestration": ORCHESTRATION_INSTRUCTIONS,
        "response_style": "tabular",
        "sample_questions": SAMPLE_QUESTIONS,
        "budget_seconds": 180,
        "budget_tokens": 24000,
        "tool_not_accessible": "accept",
        "default_tools": [
            "load_skill",
            "semantic_query",
            "semantic_search",
            "query_execute",
            "data_to_chart",
            "ml_execute",
        ],
        "default_skills": [],
        "discoverable_skills": [],
        "compiled_instructions": compiled,
        "harness_mode": "auto",
        "policy": "auto_read_only",
        "semantic_model_id": model_id,
        "semantic_model_ids": [model_id],
        "visibility": "private",
    }
    matches = [
        row
        for row in await agent_repository.list_agents(owner_name=owner)
        if row["name"] == AGENT_NAME
    ]
    if matches:
        agent = await agent_repository.update_agent(
            matches[0]["agent_id"], owner_name=owner, fields=fields
        )
        assert agent is not None
        return agent
    return await agent_repository.create_agent(owner_name=owner, fields=fields)


async def ensure_verified_queries(
    owner: str, model: dict, semantic_ir: SemanticModelIR
) -> int:
    await db.execute_system(
        "DELETE FROM NOVA_SYSTEM.CONFIG_SEMANTIC_VERIFIED_QUERIES "
        "WHERE semantic_model_id = %s AND owner_name = %s AND model_fingerprint <> %s",
        [model["semantic_model_id"], owner, semantic_ir.fingerprint],
    )
    existing = await agent_repository.list_verified_queries(
        model["semantic_model_id"], owner_name=owner
    )
    existing_keys = {(row["question"], row["model_fingerprint"]) for row in existing}
    created = 0
    compiler = SemanticCompiler()
    for item in VERIFIED_PLANS:
        key = (item["question"], semantic_ir.fingerprint)
        if key in existing_keys:
            continue
        plan = SemanticPlan.from_dict(item["plan"])
        compiled = compiler.compile(semantic_ir, plan)
        await agent_repository.create_verified_query(
            owner_name=owner,
            fields={
                "semantic_model_id": model["semantic_model_id"],
                "model_fingerprint": semantic_ir.fingerprint,
                "question": item["question"],
                "semantic_plan": plan.as_dict(),
                "verified_sql": compiled.sql,
                "expected_result_signature": item["signature"],
                "tags": item["tags"],
            },
        )
        created += 1
    return created


async def seed(owner: str) -> tuple[dict, dict, int]:
    parsed = parse_ossie(OSSIE_PATH.read_text(encoding="utf-8"))
    definition = parsed.as_dict()
    semantic_ir = SemanticModelIR.from_ossie(definition)
    validation = validate_semantic_model_ir(semantic_ir)
    if not validation.valid:
        raise ValueError("; ".join(validation.errors))

    await db.init_system_pool()
    try:
        await agent_repository.ensure_schema()
        model = await upsert_model(owner, definition, parsed.version or "0.1.1")
        agent = await upsert_agent(owner, model["semantic_model_id"])
        verified_count = await ensure_verified_queries(owner, model, semantic_ir)
        return model, agent, verified_count
    finally:
        await db.close_system_pool()


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed Nova's detailed Sales Agent demo.")
    parser.add_argument(
        "--owner", default="nova_admin", help="Nova/StarRocks owner username"
    )
    args = parser.parse_args()
    model, agent, verified_count = asyncio.run(seed(args.owner))
    print(f"semantic_model_id: {model['semantic_model_id']}")
    print(f"agent_id:          {agent['agent_id']}")
    print(f"verified_queries_created: {verified_count}")
    print(f"Open Nova Studio and select {AGENT_NAME!r}.")


if __name__ == "__main__":
    main()

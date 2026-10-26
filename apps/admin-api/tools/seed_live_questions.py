"""Seed a small, realistic question bank and rebuild the live index.

The publish flow indexes only ``approved`` / ``published`` questions from
MongoDB. A fresh environment has none, so ``questions_active`` stays empty and
student retrieval can only see textbook chunks. This tool upserts a deterministic
POC question set (30 questions: 20 高中数学 + 10 高中物理), then runs the index
half of the publish flow exactly like ``bootstrap_live_index.py``.

Question ids are stable (``qseed_math_001`` …), so re-running updates the same
documents instead of duplicating them.

Usage (from ``apps/admin-api``)::

    set -a && . ./.env.remote && set +a && ./.venv/Scripts/python.exe tools/seed_live_questions.py

Options::

    --tenant tnt_yxp     tenant to seed
    --version ""         index version suffix (default: current UTC timestamp)
    --skip-index         only upsert MongoDB, do not touch Milvus
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime
from typing import Any

from app.core.config import settings
from app.infra import mongo
from app.infra.milvus import get_vector_index
from app.modules.publish.indexer import build_version_index
from app.shared.time import utcnow

TENANT = "tnt_yxp"


def Q(  # noqa: N802 - compact literal helper for the dataset below
    qid: str,
    subject: str,
    grade: str,
    qtype: str,
    difficulty: str,
    stem: str,
    answer: str,
    analysis: str,
    options: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "id": qid,
        "subject": subject,
        "grade": grade,
        "type": qtype,
        "difficulty": difficulty,
        "stem": stem,
        "options": options or [],
        "answer": answer,
        "analysis": analysis,
    }


SEEDS: list[dict[str, Any]] = [
    # ---------------- 高中数学：立体几何 ----------------
    Q(
        "qseed_math_001", "数学", "高一", "fill_blank", "easy",
        "圆锥的底面半径为 3，高为 4，则它的体积是多少？",
        "12π",
        "V=1/3πr²h=1/3π×3²×4=12π。",
    ),
    Q(
        "qseed_math_002", "数学", "高一", "fill_blank", "easy",
        "圆柱的底面半径为 2，高为 5，则它的侧面积是多少？",
        "20π",
        "S侧=2πrh=2π×2×5=20π。",
    ),
    Q(
        "qseed_math_003", "数学", "高一", "fill_blank", "easy",
        "半径为 3 的球的体积是多少？",
        "36π",
        "V=4/3πR³=4/3π×27=36π。",
    ),
    Q(
        "qseed_math_004", "数学", "高一", "solution", "medium",
        "圆台的上、下底面半径分别为 1 和 3，高为 3，求圆台的体积。",
        "13π",
        "V=1/3π(r'²+r'r+r²)h=1/3π(1+3+9)×3=13π。",
    ),
    Q(
        "qseed_math_005", "数学", "高一", "fill_blank", "easy",
        "正四棱锥的底面边长为 4，高为 3，则它的体积是多少？",
        "16",
        "V=1/3Sh=1/3×4²×3=16。",
    ),
    Q(
        "qseed_math_006", "数学", "高一", "solution", "medium",
        "圆锥的底面半径为 3，母线长为 5，求它的侧面积和全面积。",
        "侧面积 15π，全面积 24π",
        "S侧=πrl=π×3×5=15π；底面积=πr²=9π；S全=15π+9π=24π。",
    ),
    # ---------------- 高中数学：三角函数 ----------------
    Q(
        "qseed_math_007", "数学", "高一", "fill_blank", "easy",
        "计算 sin(π/6)+cos(π/3) 的值。",
        "1",
        "sin(π/6)=1/2，cos(π/3)=1/2，所以和为 1。",
    ),
    Q(
        "qseed_math_008", "数学", "高一", "single_choice", "medium",
        "函数 y=3sin(2x+π/4) 的振幅和最小正周期分别是多少？",
        "振幅 3，周期 π",
        "振幅 |A|=3，周期 T=2π/|ω|=2π/2=π。",
        ["振幅 3，周期 π", "振幅 3，周期 2π", "振幅 1/3，周期 π", "振幅 3，周期 π/2"],
    ),
    Q(
        "qseed_math_009", "数学", "高一", "fill_blank", "easy",
        "计算 log₂8 + log₃(1/9)。",
        "1",
        "log₂8=3，log₃(1/9)=-2，所以结果为 1。",
    ),
    Q(
        "qseed_math_010", "数学", "高一", "single_choice", "easy",
        "当 x 充分大时，下列函数中增长最快的是哪一个？",
        "y=2ˣ",
        "指数函数呈爆炸性增长，x 充分大时 2ˣ 远大于 x²、x 与 ln x。",
        ["y=x", "y=x²", "y=2ˣ", "y=ln x"],
    ),
    # ---------------- 高中数学：概率统计 ----------------
    Q(
        "qseed_math_011", "数学", "高二", "fill_blank", "easy",
        "若 X 服从标准正态分布 N(0,1)，则 P(X<0) 等于多少？",
        "0.5",
        "标准正态曲线关于 x=0 对称，因此左半部分概率为 0.5。",
    ),
    Q(
        "qseed_math_012", "数学", "高二", "single_choice", "medium",
        "若 X~N(μ,σ²)，则 P(|X-μ|<3σ) 约为多少？",
        "0.9974",
        "由 3σ 原则，P(|X-μ|<3σ)≈0.9974。",
        ["0.6826", "0.9544", "0.9974", "0.9999"],
    ),
    Q(
        "qseed_math_020", "数学", "高二", "solution", "medium",
        "同时掷两枚质地均匀的骰子，求点数之和为 7 的概率。",
        "1/6",
        "样本空间共 36 个等可能结果，和为 7 的有 (1,6)(2,5)(3,4)(4,3)(5,2)(6,1) 共 6 个，概率为 6/36=1/6。",
    ),
    # ---------------- 高中数学：圆锥曲线 ----------------
    Q(
        "qseed_math_013", "数学", "高二", "fill_blank", "easy",
        "椭圆的长半轴 a=5，半焦距 c=3，求它的离心率。",
        "3/5",
        "e=c/a=3/5。",
    ),
    Q(
        "qseed_math_014", "数学", "高二", "fill_blank", "medium",
        "双曲线 x²/9 - y²/16 = 1 的渐近线方程是什么？",
        "y=±4/3x",
        "双曲线 x²/a²-y²/b²=1 的渐近线为 y=±(b/a)x，此处 a=3，b=4。",
    ),
    Q(
        "qseed_math_015", "数学", "高二", "single_choice", "easy",
        "抛物线 y²=8x 的焦点坐标是什么？",
        "(2,0)",
        "由 y²=2px 得 2p=8，p=4，焦点为 (p/2,0)=(2,0)。",
        ["(0,2)", "(2,0)", "(4,0)", "(0,4)"],
    ),
    Q(
        "qseed_math_016", "数学", "高二", "solution", "medium",
        "两圆半径分别为 2 和 3，圆心距为 5，判断两圆的位置关系。",
        "外切",
        "d=r₁+r₂=5，因此两圆外切。",
    ),
    # ---------------- 高中数学：数列 / 导数 / 向量 ----------------
    Q(
        "qseed_math_017", "数学", "高二", "solution", "medium",
        "等差数列的首项为 2，公差为 3，求第 10 项与前 10 项和。",
        "a₁₀=29，S₁₀=155",
        "a₁₀=2+9×3=29；S₁₀=10×(2+29)/2=155。",
    ),
    Q(
        "qseed_math_018", "数学", "高三", "solution", "hard",
        "求函数 f(x)=x³-3x 的极大值和极小值。",
        "极大值 f(-1)=2，极小值 f(1)=-2",
        "f'(x)=3x²-3=3(x-1)(x+1)，令 f'(x)=0 得 x=±1；x<-1 递增，-1<x<1 递减，x>1 递增，因此 f(-1)=2 为极大值，f(1)=-2 为极小值。",
    ),
    Q(
        "qseed_math_019", "数学", "高一", "single_choice", "easy",
        "已知向量 a=(1,2)，b=(2,-1)，则 a 与 b 的关系是什么？",
        "垂直",
        "a·b=1×2+2×(-1)=0，所以两向量垂直。",
        ["平行", "垂直", "夹角为 30°", "夹角为 60°"],
    ),
    # ---------------- 高中物理 ----------------
    Q(
        "qseed_phys_001", "物理", "高一", "fill_blank", "easy",
        "物体从静止开始做匀加速直线运动，加速度为 2 m/s²，3 s 末的速度和位移分别是多少？",
        "速度 6 m/s，位移 9 m",
        "v=at=2×3=6 m/s；s=1/2at²=1/2×2×9=9 m。",
    ),
    Q(
        "qseed_phys_002", "物理", "高一", "fill_blank", "easy",
        "物体从 20 m 高处自由下落（g 取 10 m/s²），落地时间和落地速度分别是多少？",
        "2 s，20 m/s",
        "h=1/2gt² 得 t=√(2h/g)=2 s；v=gt=20 m/s。",
    ),
    Q(
        "qseed_phys_003", "物理", "高一", "fill_blank", "easy",
        "质量为 2 kg 的物体在光滑水平面上受 10 N 的水平恒力作用，加速度是多少？",
        "5 m/s²",
        "由牛顿第二定律 a=F/m=10/2=5 m/s²。",
    ),
    Q(
        "qseed_phys_004", "物理", "高一", "fill_blank", "easy",
        "物体做半径为 2 m、线速度为 4 m/s 的匀速圆周运动，向心加速度是多少？",
        "8 m/s²",
        "a=v²/r=16/2=8 m/s²。",
    ),
    Q(
        "qseed_phys_005", "物理", "高一", "solution", "medium",
        "质量为 1 kg 的物体从静止开始，在 10 N 的水平力作用下沿力的方向移动 2 m，求力做的功和物体末速度（不计摩擦）。",
        "W=20 J，v=√40≈6.32 m/s",
        "W=Fs=10×2=20 J；由动能定理 1/2mv²=W，得 v=√40≈6.32 m/s。",
    ),
    Q(
        "qseed_phys_006", "物理", "高二", "solution", "medium",
        "质量为 2 kg 的小球以 3 m/s 的速度与静止的 1 kg 小球发生完全非弹性碰撞，碰后共同速度是多少？",
        "2 m/s",
        "碰撞过程动量守恒：m₁v₁=(m₁+m₂)v，得 v=2×3/3=2 m/s。",
    ),
    Q(
        "qseed_phys_007", "物理", "高二", "fill_blank", "easy",
        "在电场强度为 2×10³ N/C 的匀强电场中，电荷量为 2×10⁻⁶ C 的点电荷受到的电场力是多少？",
        "4×10⁻³ N",
        "F=qE=2×10⁻⁶×2×10³=4×10⁻³ N。",
    ),
    Q(
        "qseed_phys_008", "物理", "高二", "fill_blank", "easy",
        "阻值为 2 Ω 和 4 Ω 的两个电阻串联，接在 12 V 电源上，电路电流和 4 Ω 电阻两端电压分别是多少？",
        "2 A，8 V",
        "R总=6 Ω，I=U/R=2 A；U₂=IR₂=2×4=8 V。",
    ),
    Q(
        "qseed_phys_009", "物理", "高二", "single_choice", "medium",
        "带电粒子以速度 v 垂直进入磁感应强度为 B 的匀强磁场，其做圆周运动的半径是什么？",
        "r=mv/(qB)",
        "洛伦兹力提供向心力 qvB=mv²/r，得 r=mv/(qB)。",
        ["r=qv/(mB)", "r=mv/(qB)", "r=qB/(mv)", "r=mB/(qv)"],
    ),
    Q(
        "qseed_phys_010", "物理", "高二", "fill_blank", "medium",
        "线圈匝数为 n，磁通量在 Δt 时间内变化 ΔΦ，写出感应电动势的表达式。",
        "E=nΔΦ/Δt",
        "由法拉第电磁感应定律，E=nΔΦ/Δt。",
    ),
]


def build_documents(tenant_id: str) -> list[dict[str, Any]]:
    now = utcnow()
    docs: list[dict[str, Any]] = []
    for seed in SEEDS:
        docs.append(
            {
                "_id": seed["id"],
                "tenant_id": tenant_id,
                "book_id": "",
                "chapter_id": "",
                "knowledge_point_ids": [],
                "subject": seed["subject"],
                "stage": "high",
                "grade": seed["grade"],
                "edition": "",
                "type": seed["type"],
                "difficulty": seed["difficulty"],
                "stem": seed["stem"],
                "options": seed["options"],
                "answer": seed["answer"],
                "analysis": seed["analysis"],
                "review_status": "approved",
                "source": "seed",
                "updated_at": now,
            }
        )
    return docs


async def upsert_questions(tenant_id: str) -> int:
    collection = mongo.col("questions")
    docs = build_documents(tenant_id)
    for doc in docs:
        payload = {key: value for key, value in doc.items() if key != "_id"}
        await collection.update_one(
            {"_id": doc["_id"]},
            {"$set": payload, "$setOnInsert": {"created_at": utcnow()}},
            upsert=True,
        )
    return len(docs)


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tenant", default=TENANT)
    parser.add_argument("--version", default="", help="index version suffix")
    parser.add_argument("--skip-index", action="store_true")
    args = parser.parse_args()

    count = await upsert_questions(args.tenant)
    print(f"mongo questions upserted: {count} (tenant={args.tenant})")
    if args.skip_index:
        await mongo.close()
        return

    if not settings.milvus_uri:
        raise SystemExit("MILVUS_URI is empty; refusing to build an in-memory index")

    version = args.version or datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    print(f"building index version={version} ...")
    build = await build_version_index(tenant_id=args.tenant, version=version)
    print(
        f"built chunks_{version}: {build.chunks} chunks; "
        f"questions_{version}: {build.questions} questions"
    )
    if build.embedded_chunks or build.embedded_questions:
        print(
            f"embedded during build: {build.embedded_chunks} chunks / "
            f"{build.embedded_questions} questions ({build.provider})"
        )

    vector = get_vector_index()
    chunk_rows = await vector.stage(f"chunks_{version}", build.chunks)
    question_rows = await vector.stage(f"questions_{version}", build.questions)
    print(f"staged rows: chunks={chunk_rows} questions={question_rows}")

    await vector.commit(settings.chunks_alias, f"chunks_{version}")
    await vector.commit(settings.questions_alias, f"questions_{version}")
    print(
        f"aliases switched: {settings.chunks_alias} -> chunks_{version}, "
        f"{settings.questions_alias} -> questions_{version}"
    )
    await mongo.close()


if __name__ == "__main__":
    asyncio.run(main())

# scripts/cli.py
# 题目只读 CLI（开发 / 外部 Agent 接口）：包装 src.retrieval.question 的
# 两个纯数据库读门面——browse_questions（结构化浏览）与
# get_question_detail（题目完整详情）。设计见 docs/scripts/cli.md。
#
# 要点：
#   - 只读、无 LLM、无向量：browse / detail 全部走 SQLite 查询门面，
#     不需要 .env 里的 API Key（仅 config.toml 路径解析），不写任何数据。
#   - 双输出：默认人类可读；--json 输出纯 JSON（stdout 机器可读，
#     便于外部 Agent / shell 管道消费；错误只打 stderr，stdout 保持干净）。
#   - 退出码：0=成功（含空结果）；1=参数或查询错误（如 id 不存在）。
#   - 门面组合逻辑不在这里重复——本文件只做参数解析 + 输出格式化。
#
# 示例：
#   uv run python scripts/cli.py browse --year 2026 --region 南昌 --type 解答题
#   uv run python scripts/cli.py detail 42 --json

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from io import TextIOWrapper

from src.retrieval.question import QuestionDetail, QuestionHit, browse_questions, get_question_detail


# ═══════════════════════════════════════════════════════════════════════════════
# 参数解析
# ═══════════════════════════════════════════════════════════════════════════════


def _build_parser() -> argparse.ArgumentParser:
    """构造 argparse 解析器（browse / detail 两个子命令）。"""
    parser = argparse.ArgumentParser(
        prog="cli.py",
        description="Gaokao RAG 题目只读 CLI（结构化浏览 / 完整详情，纯 SQLite，不涉语义检索）",
        epilog=(
            "示例:\n"
            "  uv run python scripts/cli.py browse --year 2026 --region 南昌 --type 解答题\n"
            "  uv run python scripts/cli.py browse --topic 椭圆 --limit 5 --json\n"
            "  uv run python scripts/cli.py detail 42"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True, metavar="{browse,detail}")

    # ── browse：对应 browse_questions(filters) ──────────────────────
    p_browse = sub.add_parser("browse", help="结构化浏览题目（年份/考区/题型/知识点等组合过滤）")
    p_browse.add_argument("--subject", help="学科，如 数学")
    p_browse.add_argument("--source-type", dest="source_type",
                          help="来源类型：exam / homework / special_topic / error_book")
    p_browse.add_argument("--year", type=int, help="年份，如 2026")
    p_browse.add_argument("--month", type=int, help="月份 1-12")
    p_browse.add_argument("--type", dest="question_type",
                          help="题型：单选题 / 多选题 / 填空题 / 解答题")
    p_browse.add_argument("--region", help="考区单值（对考区层级列表做包含匹配），如 南昌")
    p_browse.add_argument("--topic", help="知识点规范名（经 question_topics 反查），如 椭圆")
    p_browse.add_argument("--file-id", dest="file_id", type=int,
                          help="来源试卷/作业的 files.id（列出整卷题目）")
    p_browse.add_argument("--limit", type=int, help="返回条数上限")
    p_browse.add_argument("--json", dest="as_json", action="store_true",
                          help="输出纯 JSON（QuestionHit 列表）")

    # ── detail：对应 get_question_detail(question_id) ───────────────
    p_detail = sub.add_parser("detail", help="按题目 ID 查看完整详情（题干+答案+解析+知识点+图片）")
    p_detail.add_argument("question_id", type=int, help="questions 表主键 ID")
    p_detail.add_argument("--json", dest="as_json", action="store_true",
                          help="输出纯 JSON（QuestionDetail）")

    return parser


def _browse_filters(args: argparse.Namespace) -> dict:
    """argparse 命名 → 门面 filters 键名，丢弃未提供的 None 值。"""
    mapping = {
        "subject": args.subject,
        "source_type": args.source_type,
        "exam_year": args.year,
        "exam_month": args.month,
        "question_type": args.question_type,
        "exam_region": args.region,
        "topic_name": args.topic,
        "file_id": args.file_id,
        "limit": args.limit,
    }
    return {key: value for key, value in mapping.items() if value is not None}


# ═══════════════════════════════════════════════════════════════════════════════
# 人类可读输出
# ═══════════════════════════════════════════════════════════════════════════════


def _print_hit(index: int, hit: QuestionHit) -> None:
    """打印一条 QuestionHit 摘要行（列表场景，题号/考区/图标记 + 题干摘要）。"""
    meta = [
        f"id={hit.question_id}",
        hit.doc_id,
        str(hit.exam_year or "?") + (f"-{hit.exam_month:02d}" if hit.exam_month else ""),
        hit.question_number or "无题号",
        hit.question_type or "?",
    ]
    line1 = f"{index:>3}. " + "  ".join(meta)
    if hit.exam_regions:
        line1 += "  [" + "/".join(hit.exam_regions) + "]"
    if hit.has_image:
        line1 += "  🖼"
    print(line1)
    print(f"     {hit.content_text}")


def _print_detail(d: QuestionDetail) -> None:
    """打印完整详情（溯源头 + 题干/答案/解析分块）。"""
    head = (
        f"id={d.question_id}  {d.doc_id}  {d.subject}/{d.source_type}  "
        f"{d.question_type or '?'}  {d.exam_year or '?'}"
        + (f"-{d.exam_month:02d}" if d.exam_month else "")
        + f"  {d.question_number or '无题号'}"
    )
    print(head)
    if d.exam_regions:
        print("考区: " + " / ".join(d.exam_regions))
    print(f"来源: file_id={d.file_id if d.file_id is not None else '无'}")
    print("知识点: " + ("、".join(d.topic_names) if d.topic_names else "无"))
    print("图片: " + ("、".join(f"file_id={i}" for i in d.image_file_ids) if d.image_file_ids else "无"))
    print("── 题干 " + "─" * 44)
    print(d.content_text)
    print("── 答案 " + "─" * 44)
    print(d.answer_text or "（无）")
    print("── 解析 " + "─" * 44)
    print(d.analysis_text or "（无）")


# ═══════════════════════════════════════════════════════════════════════════════
# 子命令入口
# ═══════════════════════════════════════════════════════════════════════════════


def _cmd_browse(args: argparse.Namespace) -> int:
    """browse 子命令：filters 组装 → browse_questions → 输出。"""
    hits = browse_questions(_browse_filters(args))
    if args.as_json:
        print(json.dumps([asdict(h) for h in hits], ensure_ascii=False))
    elif not hits:
        print("没有符合条件的题目（0 条）。")
    else:
        print(f"共 {len(hits)} 题：")
        for i, hit in enumerate(hits, start=1):
            _print_hit(i, hit)
    return 0


def _cmd_detail(args: argparse.Namespace) -> int:
    """detail 子命令：get_question_detail → 输出。"""
    detail = get_question_detail(args.question_id)
    if args.as_json:
        print(json.dumps(asdict(detail), ensure_ascii=False))
    else:
        _print_detail(detail)
    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI 主入口：stdout UTF-8 兜底 + 门面 ValueError → 友好报错。

    Args:
        argv: 参数列表，``None`` 时取 ``sys.argv[1:]``。

    Returns:
        退出码：0=成功；1=查询错误（id 不存在 / filters 非法）。
        argparse 自身的参数错误按标准行为退出码 2。
    """
    # Git Bash / Windows 终端中文输出兜底（不依赖 PYTHONIOENCODING）；
    # stderr 同步兜底——错误信息中文在 GBK 控制台下同样会乱码
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, TextIOWrapper):
            stream.reconfigure(encoding="utf-8")

    args = _build_parser().parse_args(argv)
    handler = _cmd_browse if args.command == "browse" else _cmd_detail
    try:
        return handler(args)
    except ValueError as exc:  # 门面业务校验（id 不存在 / 不支持的过滤键）
        print(f"[错误] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

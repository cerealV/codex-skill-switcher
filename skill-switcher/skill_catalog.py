"""Provenance comes from local records; purpose groups are a reviewed catalog."""
import json
from pathlib import Path
import re

CATALOG = json.loads(Path(__file__).with_name("catalog.json").read_text(encoding="utf-8"))
CATEGORIES = list(CATALOG["categories"])
CATEGORY_BY_NAME = {name: category for category, names in CATALOG["categories"].items() for name in names}


def github_repo(value):
    match = re.fullmatch(r"(?:https://github\.com/)?([\w.-]+/[\w.-]+?)(?:\.git)?/?", value or "")
    return match[1] if match else None


def classify_purpose(skill, package):
    name = skill["name"]
    if package == "cloudbase":
        return "小程序与云开发"
    if package == "boss":
        if name.startswith("ui-designer/"):
            return "界面设计与前端"
        if name.startswith(("pm/", "scrum-master/")) or name in ("boss", "brainstorming"):
            return "产品规划与工作流程"
        return "开发工程与测试"
    return CATEGORY_BY_NAME.get(name, "其他 / 待分类")


def enrich_skills(skills, home):
    lock_file = Path.home() / ".agents/.skill-lock.json"
    lock = json.loads(lock_file.read_text(encoding="utf-8")) if lock_file.exists() else {}
    records = lock.get("skills", {})
    result = []
    for raw in skills:
        skill = dict(raw)
        path = Path(skill["path"])
        package = ""
        record = None
        try:
            relative = path.relative_to(home / "skills")
            package = relative.parts[0]
            record = CATALOG["packages"].get(package)
        except ValueError:
            pass
        source, repo, evidence = "来源未确认（本地）", None, "没有匹配的本地安装记录；文档中的依赖链接不作为下载来源。"
        agents_root = Path.home() / ".agents/skills"
        try:
            relative = path.relative_to(agents_root)
            entry = records.get(relative.parts[0], {})
            if entry.get("sourceType") == "github":
                repo = github_repo(entry.get("sourceUrl")) or github_repo(entry.get("source"))
                if repo:
                    source = repo
                    evidence = f"安装锁记录：{lock_file}；安装时间 {entry.get('installedAt', '未记录')}"
        except ValueError:
            pass
        if record and repo is None:
            repo = record["repo"]
            source = repo + "（预置映射）"
            evidence = record["evidence"]
        if package:
            source_file = home / "skills" / package / "references/source.json"
            if source_file.is_file():
                local = json.loads(source_file.read_text(encoding="utf-8"))
                local_repo = github_repo(local.get("repository"))
                if local_repo:
                    repo, source = local_repo, local_repo + "（本地改编）"
                    evidence = f"本地来源记录：{source_file}；{local.get('adaptation', '')}；素材下载时间 {local.get('retrieved', '未记录')}"
        if skill.get("pluginId"):
            repo = None
            source = "插件：" + skill["pluginId"]
            evidence = "Codex 返回的插件标识；插件标识不等于 GitHub 仓库。"
        elif skill.get("scope") == "system":
            repo, source, evidence = None, "OpenAI 内置", "Codex 返回的 system 来源，不推断下载仓库。"
        skill.update(source_group=source, source_repo=repo, source_evidence=evidence,
                     purpose=classify_purpose(skill, package))
        result.append(skill)
    return result


def group_key(skill, mode):
    if mode == "用途 → GitHub 来源":
        return skill["purpose"], skill["source_group"]
    return skill["source_group"], skill["purpose"]


def ordered_groups(skills, mode):
    groups = {}
    for skill in skills:
        groups.setdefault(group_key(skill, mode), []).append(skill)
    # Large source collections come first; purposes follow their explicit catalog order.
    totals = {}
    for (primary, _), members in groups.items():
        totals[primary] = totals.get(primary, 0) + len(members)
    rank = {category: i for i, category in enumerate(CATEGORIES)}
    def key(pair):
        primary, secondary = pair
        if mode == "用途 → GitHub 来源":
            return rank.get(primary, len(rank)), -totals[primary], -len(groups[pair]), secondary
        unknown = primary.startswith("来源未确认")
        return unknown, -totals[primary], primary, rank.get(secondary, len(rank))
    return [(pair, groups[pair]) for pair in sorted(groups, key=key)]

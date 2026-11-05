"""
Query tokenizer for SAP SF Knowledge Base.
Handles mixed Chinese/English queries by:
  1. Splitting English words normally
  2. Segmenting Chinese with jieba
  3. Expanding Chinese SAP terms to English equivalents
"""
import re

# Chinese SAP SF terms → English equivalents for BM25 matching
ZH_EN_SAP = {
    # Core modules
    "入职": ["onboarding"],
    "离职": ["offboarding"],
    "招聘": ["recruiting"],
    "学习": ["learning"],
    "薪酬": ["compensation"],
    "薪资": ["salary", "compensation"],
    "绩效": ["performance"],
    "继任": ["succession"],
    "职业": ["career"],
    "福利": ["benefits"],
    "工资": ["salary", "payroll"],
    "奖金": ["bonus"],
    # Platform / tech
    "权限": ["permission", "rbp"],
    "角色权限": ["role-based permission", "rbp"],
    "权限角色": ["permission role"],
    "权限组": ["permission group"],
    "角色": ["role"],
    "元数据": ["metadata", "mdf"],
    "对象": ["object"],
    "字段": ["field"],
    "表单": ["form"],
    "模板": ["template"],
    "工作流": ["workflow"],
    "流程": ["process", "workflow"],
    "规则": ["rule"],
    "条件": ["condition"],
    "触发": ["trigger"],
    "事件": ["event"],
    "集成": ["integration"],
    "自定义": ["custom", "customization"],
    "单点登录": ["sso", "single sign-on"],
    "登录": ["login"],
    "安全": ["security"],
    "合规": ["compliance"],
    "报告": ["report"],
    "报表": ["report"],
    "导入": ["import"],
    "导出": ["export"],
    "启用": ["enable", "activate"],
    "禁用": ["disable"],
    "通知": ["notification"],
    "邮件": ["email"],
    "任务": ["task"],
    "步骤": ["step"],
    "审批": ["approval"],
    "配置": ["configuration", "configure"],
    "设置": ["setting", "configuration"],
    # People / org
    "员工": ["employee"],
    "新员工": ["new hire", "new employee"],
    "候选人": ["candidate"],
    "管理员": ["administrator", "admin"],
    "用户": ["user"],
    "员工中心": ["employee central", "ec"],
    "人力资源": ["hr", "human resources"],
    "职位": ["position"],
    "部门": ["department"],
    "公司": ["company"],
    "组织": ["organization"],
    # Talent / learning
    "目标": ["goal"],
    "评估": ["evaluation", "assessment"],
    "反馈": ["feedback"],
    "技能": ["skill"],
    "培训": ["training"],
    "课程": ["course"],
    "认证": ["certification"],
    "面试": ["interview"],
    "录用": ["offer"],
    # Generic / stop-word-like (map to nothing)
    "如何": [],
    "怎么": [],
    "怎样": [],
    "什么": [],
    "是否": [],
    "可以": [],
    "在哪": [],
    "哪里": [],
    "数据": ["data"],
    "文档": ["document"],
    "信息": ["information"],
    "日期": ["date"],
    "时间": ["time"],
}


def _has_chinese(text: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", text))


_jieba_initialized = False


def _init_jieba():
    global _jieba_initialized
    if _jieba_initialized:
        return
    import jieba
    jieba.setLogLevel("WARNING")
    # Register compound SAP terms so jieba treats them as single units
    for term in ZH_EN_SAP:
        if len(term) > 1:
            jieba.add_word(term, freq=10000)
    _jieba_initialized = True


def tokenize_query(text: str) -> list[str]:
    """
    Tokenize a query for BM25 retrieval.
    English words → lowercased split.
    Chinese segments → jieba + SAP term expansion to English.
    """
    tokens: set[str] = set()

    # Always extract English/ASCII tokens
    english_part = re.sub(r"[\u4e00-\u9fff]+", " ", text)
    for t in english_part.lower().split():
        t = t.strip(".,?!:;\"'()")
        if t:
            tokens.add(t)

    if _has_chinese(text):
        import jieba
        _init_jieba()
        for seg in jieba.lcut(text):
            seg = seg.strip()
            if not seg or not _has_chinese(seg):
                continue
            if seg in ZH_EN_SAP:
                for en in ZH_EN_SAP[seg]:
                    tokens.update(en.lower().split())

    return list(tokens)

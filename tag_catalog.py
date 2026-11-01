"""画像标签目录：统一模型候选、中文显示与正向分类。"""

MAX_LLM_TAGS = 24

# (中文名, 维度)。只描述可观察行为，不推断身份或敏感个人属性。
TAG_CATALOG = {
    "spam_suspect": ("刷屏嫌疑", "内容关注"),
    "ad_suspect": ("广告嫌疑", "内容关注"),
    "troll": ("抬杠/钓鱼", "内容关注"),
    "nsfw_tendency": ("不适宜内容", "内容关注"),
    "political_sensitive": ("敏感倾向", "内容关注"),
    "scam_suspect": ("诈骗嫌疑", "内容关注"),
    "repetitive": ("重复内容", "表达方式"),
    "friendly": ("语气友好", "协作习惯"),
    "helpful": ("乐于助人", "协作习惯"),
    "normal": ("表现正常", "互动习惯"),
    "concise": ("表达简洁", "表达方式"),
    "detail_oriented": ("注重细节", "表达方式"),
    "structured_expression": ("表达有条理", "表达方式"),
    "direct_expression": ("表达直接", "表达方式"),
    "humorous": ("幽默表达", "表达方式"),
    "playful": ("偏好玩笑互动", "表达方式"),
    "storytelling": ("喜欢叙事", "表达方式"),
    "question_driven": ("善于追问", "表达方式"),
    "example_driven": ("习惯举例", "表达方式"),
    "reflective": ("习惯复盘", "表达方式"),
    "tech_interest": ("关注技术", "兴趣话题"),
    "coding_interest": ("关注编程", "兴趣话题"),
    "ai_interest": ("关注人工智能", "兴趣话题"),
    "gaming_interest": ("关注游戏", "兴趣话题"),
    "anime_interest": ("关注动漫", "兴趣话题"),
    "music_interest": ("关注音乐", "兴趣话题"),
    "reading_interest": ("喜欢阅读交流", "兴趣话题"),
    "creative_interest": ("喜欢创作交流", "兴趣话题"),
    "science_interest": ("关注科普", "兴趣话题"),
    "daily_sharing": ("分享日常", "兴趣话题"),
    "knowledge_sharing": ("主动分享知识", "协作习惯"),
    "constructive_feedback": ("提供建设性反馈", "协作习惯"),
    "evidence_seeking": ("重视依据", "协作习惯"),
    "verification_habit": ("习惯核实信息", "协作习惯"),
    "patient_explainer": ("耐心解释", "协作习惯"),
    "collaborative": ("愿意协作", "协作习惯"),
    "acknowledges_correction": ("愿意接受纠正", "协作习惯"),
    "boundary_respecting": ("尊重互动边界", "协作习惯"),
    "solution_oriented": ("偏好解决问题", "协作习惯"),
    "resource_sharing": ("分享实用资源", "协作习惯"),
    "proactive_conversation": ("主动开启话题", "互动习惯"),
    "responsive": ("回应他人话题", "互动习惯"),
    "follow_up": ("关注后续进展", "互动习惯"),
    "casual_chat": ("偏好轻松闲聊", "互动习惯"),
    "deep_discussion": ("偏好深入讨论", "互动习惯"),
    "task_focused": ("交流目标明确", "互动习惯"),
    "step_by_step_preference": ("偏好分步说明", "互动习惯"),
    "brief_answer_preference": ("偏好简短回答", "互动习惯"),
    "context_provider": ("主动补充背景", "互动习惯"),
    "clarification_seeking": ("习惯澄清歧义", "互动习惯"),
    "formal_expression": ("偏好正式表达", "表达方式"),
    "colloquial_expression": ("偏好口语表达", "表达方式"),
    "analogy_expression": ("习惯使用类比", "表达方式"),
    "visual_expression": ("习惯配图说明", "表达方式"),
    "summary_expression": ("习惯总结要点", "表达方式"),
    "film_interest": ("关注影视", "兴趣话题"),
    "photography_interest": ("关注摄影", "兴趣话题"),
    "design_interest": ("关注设计", "兴趣话题"),
    "hardware_interest": ("关注硬件", "兴趣话题"),
    "open_source_interest": ("关注开源项目", "兴趣话题"),
    "automation_interest": ("关注自动化工具", "兴趣话题"),
    "language_interest": ("关注语言学习", "兴趣话题"),
    "history_interest": ("关注历史话题", "兴趣话题"),
    "literature_interest": ("关注文学", "兴趣话题"),
    "cooking_interest": ("关注烹饪", "兴趣话题"),
    "travel_interest": ("关注旅行", "兴趣话题"),
    "sports_interest": ("关注运动话题", "兴趣话题"),
    "nature_interest": ("关注自然生态", "兴趣话题"),
    "board_games_interest": ("关注桌游", "兴趣话题"),
    "handcraft_interest": ("关注手工制作", "兴趣话题"),
    "hands_on_learning": ("偏好动手实践", "学习方式"),
    "theory_first_learning": ("偏好先理解原理", "学习方式"),
    "project_based_learning": ("偏好项目式学习", "学习方式"),
    "trial_and_error_learning": ("通过试错探索", "学习方式"),
    "documentation_learning": ("习惯查阅文档", "学习方式"),
    "comparison_learning": ("通过对比理解", "学习方式"),
    "fundamentals_learning": ("重视基础概念", "学习方式"),
    "notes_learning": ("习惯整理笔记", "学习方式"),
    "cross_topic_learning": ("喜欢跨话题联想", "学习方式"),
    "guided_learning": ("偏好引导式学习", "学习方式"),
    "tradeoff_aware": ("关注方案取舍", "决策习惯"),
    "alternatives_seeking": ("习惯比较备选方案", "决策习惯"),
    "constraint_aware": ("主动说明约束条件", "决策习惯"),
    "cost_conscious": ("关注使用成本", "决策习惯"),
    "efficiency_seeking": ("重视处理效率", "决策习惯"),
    "quality_seeking": ("重视完成质量", "决策习惯"),
    "reliability_seeking": ("重视稳定可靠", "决策习惯"),
    "reversible_steps": ("偏好可回退方案", "决策习惯"),
    "small_trial_first": ("偏好先小范围试用", "决策习惯"),
    "long_term_planning": ("讨论长期规划", "决策习惯"),
    "privacy_conscious": ("主动关注隐私", "边界与分歧"),
    "consent_seeking": ("互动前征求同意", "边界与分歧"),
    "permission_clarifying": ("主动确认权限", "边界与分歧"),
    "accepts_refusal": ("能够接受拒绝", "边界与分歧"),
    "negotiates_limits": ("倾向协商边界", "边界与分歧"),
    "states_own_limits": ("明确表达自身边界", "边界与分歧"),
    "calm_disagreement": ("平和表达不同意见", "边界与分歧"),
    "perspective_taking": ("尝试理解不同立场", "边界与分歧"),
    "deescalating": ("主动缓和争执", "边界与分歧"),
    "topic_reset": ("分歧后主动换题", "边界与分歧"),
    "feedback_specific": ("反馈具体明确", "反馈习惯"),
    "repro_steps_provider": ("提供问题复现步骤", "反馈习惯"),
    "test_results_sharing": ("主动反馈测试结果", "反馈习惯"),
    "iterative_refinement": ("习惯多轮迭代", "反馈习惯"),
    "acceptance_criteria": ("明确验收标准", "反馈习惯"),
    "limitations_reporting": ("主动说明方案局限", "反馈习惯"),
    "uncertainty_acknowledging": ("明确表达不确定性", "反馈习惯"),
    "error_correction": ("主动订正先前信息", "反馈习惯"),
    "progress_reporting": ("主动同步进展", "反馈习惯"),
    "confirmation_seeking": ("行动前倾向再次确认", "反馈习惯"),
    "welcomes_newcomers": ("主动欢迎新成员", "社群参与"),
    "discussion_coordination": ("主动协调讨论", "社群参与"),
    "event_participation": ("参与群内活动", "社群参与"),
    "topic_connection": ("帮助衔接讨论话题", "社群参与"),
    "group_resource_curation": ("整理群内共享资料", "社群参与"),
    "credits_contributors": ("主动注明贡献来源", "社群参与"),
    "rule_clarifying": ("主动了解群规", "社群参与"),
    "public_qa": ("参与公开答疑", "社群参与"),
    "experience_sharing": ("分享实践经验", "社群参与"),
    "discussion_recap": ("帮助回顾讨论结论", "社群参与"),
}

LLM_TAGS = frozenset(TAG_CATALOG)
SEMANTIC_RISK_TAGS = frozenset({
    "spam_suspect", "ad_suspect", "troll", "nsfw_tendency",
    "political_sensitive", "scam_suspect",
})
POSITIVE_TAGS = frozenset({
    "friendly", "helpful", "normal", "knowledge_sharing", "constructive_feedback",
    "patient_explainer", "collaborative", "acknowledges_correction",
    "boundary_respecting", "resource_sharing",
})


def tag_prompt_catalog() -> str:
    groups = {}
    for tag, (label, dimension) in TAG_CATALOG.items():
        groups.setdefault(dimension, []).append(f"{tag}（{label}）")
    return "\n".join(f"{dimension}：" + "、".join(items) for dimension, items in groups.items())


def select_profile_tags(items: list[dict]) -> list[dict]:
    """接收已清洗标签；超额时保留风险项，再按维度轮选有依据的描述项。"""
    risk = [item for item in items if item["tag"] in SEMANTIC_RISK_TAGS]
    descriptive = [item for item in items if item["tag"] not in SEMANTIC_RISK_TAGS]
    if len(items) <= MAX_LLM_TAGS:
        return risk + descriptive
    groups = {}
    for item in sorted(descriptive, key=lambda value: value["confidence"], reverse=True):
        dimension = TAG_CATALOG[item["tag"]][1]
        groups.setdefault(dimension, []).append(item)
    selected = risk[:MAX_LLM_TAGS]
    index = 0
    while len(selected) < MAX_LLM_TAGS:
        candidates = [values[index] for values in groups.values() if len(values) > index]
        if not candidates:
            break
        candidates.sort(key=lambda value: value["confidence"], reverse=True)
        selected.extend(candidates[:MAX_LLM_TAGS - len(selected)])
        index += 1
    return selected

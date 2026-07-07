"""Curated habit templates used as the knowledge base for RAG recommendations.

Each template has a ``name``, ``frequency``, ``time_period``, ``icon`` (Font Awesome),
``category``, and ``tags`` for semantic matching.
"""

HABIT_TEMPLATES = [
    # ── Health ────────────────────────────────────────────────
    {"name": "早起", "frequency": "Every day this week", "time_period": "After waking up",
     "icon": "fas fa-sun", "category": "health", "tags": ["morning", "discipline", "sleep", "健康"]},
    {"name": "早睡", "frequency": "Every day this week", "time_period": "Before bedtime",
     "icon": "fas fa-bed", "category": "health", "tags": ["sleep", "rest", "health", "睡眠"]},
    {"name": "喝 8 杯水", "frequency": "Every day this week", "time_period": "Any time",
     "icon": "fas fa-tint", "category": "health", "tags": ["water", "hydration", "health", "饮水"]},
    {"name": "晨间冥想", "frequency": "Every day this week", "time_period": "After waking up",
     "icon": "fas fa-spa", "category": "health", "tags": ["meditation", "mindfulness", "mental", "冥想"]},
    {"name": "限时进食 16:8", "frequency": "Every day this week", "time_period": "Any time",
     "icon": "fas fa-clock", "category": "health", "tags": ["diet", "fasting", "nutrition", "饮食"]},
    {"name": "饭后散步 15 分钟", "frequency": "Every day this week", "time_period": "Noon",
     "icon": "fas fa-walking", "category": "health", "tags": ["walk", "digestion", "light exercise"]},
    {"name": "记录饮食", "frequency": "Every day this week", "time_period": "Evening",
     "icon": "fas fa-utensils", "category": "health", "tags": ["diet", "nutrition", "tracking"]},

    # ── Fitness ────────────────────────────────────────────────
    {"name": "跑步", "frequency": "3 times a week", "time_period": "Morning",
     "icon": "fas fa-running", "category": "fitness", "tags": ["run", "cardio", "exercise", "跑步"]},
    {"name": "力量训练", "frequency": "3 times a week", "time_period": "Afternoon",
     "icon": "fas fa-dumbbell", "category": "fitness", "tags": ["gym", "strength", "weights"]},
    {"name": "瑜伽", "frequency": "4 times a week", "time_period": "Morning",
     "icon": "fas fa-pray", "category": "fitness", "tags": ["yoga", "flexibility", "stretch"]},
    {"name": "游泳", "frequency": "2 times a week", "time_period": "Afternoon",
     "icon": "fas fa-swimmer", "category": "fitness", "tags": ["swim", "cardio", "low impact"]},
    {"name": "骑自行车", "frequency": "2 times a week", "time_period": "Afternoon",
     "icon": "fas fa-bicycle", "category": "fitness", "tags": ["cycling", "cardio", "outdoor"]},
    {"name": "跳绳", "frequency": "5 times a week", "time_period": "Morning",
     "icon": "fas fa-jump", "category": "fitness", "tags": ["jump rope", "cardio", "HIIT"]},
    {"name": "拉伸放松", "frequency": "Every day this week", "time_period": "Before bedtime",
     "icon": "fas fa-hand-peace", "category": "fitness", "tags": ["stretch", "flexibility", "recovery"]},

    # ── Learning ──────────────────────────────────────────────
    {"name": "阅读 30 分钟", "frequency": "Every day this week", "time_period": "Evening",
     "icon": "fas fa-book", "category": "learning", "tags": ["read", "knowledge", "self-improvement", "阅读"]},
    {"name": "学习外语", "frequency": "5 times a week", "time_period": "Morning",
     "icon": "fas fa-language", "category": "learning", "tags": ["language", "study", "practice"]},
    {"name": "写日记", "frequency": "Every day this week", "time_period": "Before bedtime",
     "icon": "fas fa-pen", "category": "learning", "tags": ["journal", "writing", "reflection"]},
    {"name": "刷算法题", "frequency": "3 times a week", "time_period": "Morning",
     "icon": "fas fa-laptop-code", "category": "learning", "tags": ["coding", "algorithm", "leetcode"]},
    {"name": "听播客", "frequency": "5 times a week", "time_period": "After waking up",
     "icon": "fas fa-podcast", "category": "learning", "tags": ["podcast", "learn", "commute"]},
    {"name": "练习写作", "frequency": "3 times a week", "time_period": "Evening",
     "icon": "fas fa-feather-alt", "category": "learning", "tags": ["writing", "creative", "blog"]},
    {"name": "看纪录片", "frequency": "2 times a week", "time_period": "Evening",
     "icon": "fas fa-film", "category": "learning", "tags": ["documentary", "knowledge", "watch"]},

    # ── Productivity ──────────────────────────────────────────
    {"name": "番茄工作法", "frequency": "5 times a week", "time_period": "Morning",
     "icon": "fas fa-clock", "category": "productivity", "tags": ["pomodoro", "focus", "time management"]},
    {"name": "每日计划", "frequency": "Every day this week", "time_period": "After waking up",
     "icon": "fas fa-list-check", "category": "productivity", "tags": ["plan", "organize", "GTD"]},
    {"name": "整理工作区", "frequency": "Once a week", "time_period": "Morning",
     "icon": "fas fa-broom", "category": "productivity", "tags": ["clean", "organize", "workspace"]},
    {"name": "复盘当日", "frequency": "Every day this week", "time_period": "Before bedtime",
     "icon": "fas fa-rotate-left", "category": "productivity", "tags": ["review", "reflect", "improve"]},
    {"name": "批量处理邮件", "frequency": "3 times a week", "time_period": "Morning",
     "icon": "fas fa-envelope", "category": "productivity", "tags": ["email", "batch", "communication"]},
    {"name": "断网专注 2 小时", "frequency": "5 times a week", "time_period": "Morning",
     "icon": "fas fa-wifi-slash", "category": "productivity", "tags": ["focus", "deep work", "distraction free"]},

    # ── Mindfulness ───────────────────────────────────────────
    {"name": "感恩练习", "frequency": "Every day this week", "time_period": "Before bedtime",
     "icon": "fas fa-heart", "category": "mindfulness", "tags": ["gratitude", "mindset", "positivity"]},
    {"name": "深呼吸 5 分钟", "frequency": "Every day this week", "time_period": "After waking up",
     "icon": "fas fa-wind", "category": "mindfulness", "tags": ["breathing", "calm", "anxiety"]},
    {"name": "散步冥想", "frequency": "5 times a week", "time_period": "Afternoon",
     "icon": "fas fa-person-walking", "category": "mindfulness", "tags": ["walk", "meditate", "nature"]},
    {"name": "数字排毒", "frequency": "Once a week", "time_period": "Evening",
     "icon": "fas fa-mobile-screen-button", "category": "mindfulness", "tags": ["digital detox", "screen", "unplug"]},
    {"name": "自我肯定", "frequency": "Every day this week", "time_period": "After waking up",
     "icon": "fas fa-star", "category": "mindfulness", "tags": ["affirmation", "confidence", "positive"]},
    {"name": "泡杯茶放空", "frequency": "Every day this week", "time_period": "Afternoon",
     "icon": "fas fa-mug-hot", "category": "mindfulness", "tags": ["tea", "relax", "break"]},

    # ── Social ────────────────────────────────────────────────
    {"name": "给家人打电话", "frequency": "3 times a week", "time_period": "Evening",
     "icon": "fas fa-phone", "category": "social", "tags": ["family", "call", "connect"]},
    {"name": "约朋友见面", "frequency": "Once a week", "time_period": "Afternoon",
     "icon": "fas fa-user-friends", "category": "social", "tags": ["friends", "socialize", "hangout"]},
    {"name": "参加社交活动", "frequency": "Once a week", "time_period": "Evening",
     "icon": "fas fa-users", "category": "social", "tags": ["networking", "events", "community"]},
    {"name": "做志愿者", "frequency": "Once a week", "time_period": "Morning",
     "icon": "fas fa-hand-holding-heart", "category": "social", "tags": ["volunteer", "community", "help"]},
    {"name": "发一条感谢信息", "frequency": "3 times a week", "time_period": "Evening",
     "icon": "fas fa-message", "category": "social", "tags": ["gratitude", "message", "connect"]},
]

# Pre-computed categories for filtering
CATEGORIES = {t["category"] for t in HABIT_TEMPLATES}

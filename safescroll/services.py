from collections import Counter
from datetime import date, timedelta

from safescroll.extensions import db
from safescroll.models import DailyUsageStat, Device, User, ViewingMode
from safescroll.utils import utcnow


STARTER_MODES = (
    {
        "name": "Study",
        "color": "#14B8A6",
        "icon": "📚",
        "description": "Prioritize learning and reduce entertainment distractions.",
        "preferred_categories": ["Python", "AI", "Education"],
        "blocked_categories": ["Music", "Dance", "Gaming"],
        "strictness": 4,
        "is_active": True,
    },
    {
        "name": "Child",
        "color": "#3B82F6",
        "icon": "👶",
        "description": "A safer, age-appropriate feed for younger viewers.",
        "preferred_categories": ["Kids Learning", "Science"],
        "blocked_categories": ["Adult", "Violence", "Pranks"],
        "strictness": 5,
    },
    {
        "name": "Fun",
        "color": "#F59E0B",
        "icon": "🎉",
        "description": "Entertainment with essential safety filters preserved.",
        "preferred_categories": ["Travel", "Comedy"],
        "blocked_categories": ["NSFW", "Violence"],
        "strictness": 2,
    },
    {
        "name": "Islamic",
        "color": "#8B5CF6",
        "icon": "🕌",
        "description": "Prioritize Quran, Hadith, and beneficial Islamic content.",
        "preferred_categories": ["Quran", "Hadith"],
        "blocked_categories": ["Music", "Dance"],
        "strictness": 4,
    },
)


def create_starter_modes(user: User) -> list[ViewingMode]:
    modes = []
    for data in STARTER_MODES:
        mode = ViewingMode(user=user, **data)
        if mode.is_active:
            mode.last_used_at = utcnow()
        modes.append(mode)
    db.session.add_all(modes)
    return modes


def format_duration(seconds: int) -> str:
    seconds = max(0, int(seconds or 0))
    hours, remainder = divmod(seconds, 3600)
    minutes = remainder // 60
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes} min"


def dashboard_data(user: User) -> dict:
    today = date.today()
    week_start = today - timedelta(days=6)
    stats = (
        DailyUsageStat.query.filter(
            DailyUsageStat.user_id == user.id,
            DailyUsageStat.date >= week_start,
            DailyUsageStat.date <= today,
        )
        .order_by(DailyUsageStat.date.asc())
        .all()
    )

    by_date: dict[date, dict[str, int]] = {}
    for stat in stats:
        bucket = by_date.setdefault(
            stat.date, {"filtered": 0, "relevant": 0, "time_saved_seconds": 0}
        )
        bucket["filtered"] += stat.filtered_count
        bucket["relevant"] += stat.relevant_count
        bucket["time_saved_seconds"] += stat.estimated_seconds_saved

    today_values = by_date.get(
        today, {"filtered": 0, "relevant": 0, "time_saved_seconds": 0}
    )
    metrics = {
        "filtered_today": today_values["filtered"],
        "relevant_today": today_values["relevant"],
        "time_saved_seconds": today_values["time_saved_seconds"],
        "time_saved_label": format_duration(today_values["time_saved_seconds"]),
    }

    weekly_activity = []
    maximum_filtered = max(
        (values["filtered"] for values in by_date.values()), default=0
    )
    for offset in range(7):
        day = week_start + timedelta(days=offset)
        values = by_date.get(
            day, {"filtered": 0, "relevant": 0, "time_saved_seconds": 0}
        )
        weekly_activity.append(
            {
                "date": day.isoformat(),
                "label": day.strftime("%a"),
                "day": day.strftime("%a"),
                "percent": round((values["filtered"] / maximum_filtered) * 100)
                if maximum_filtered
                else 0,
                **values,
            }
        )

    connected_device = (
        Device.query.filter_by(
            user_id=user.id, device_type="extension", revoked_at=None
        )
        .order_by(Device.last_seen_at.desc())
        .first()
    )
    previously_used_modes = [
        mode for mode in user.modes if not mode.is_active and mode.last_used_at
    ]
    recent_mode = max(
        previously_used_modes,
        key=lambda mode: mode.last_used_at,
        default=user.active_mode,
    )
    if recent_mode is None:
        recent_mode = max(
            user.modes,
            key=lambda mode: mode.last_used_at or mode.created_at,
            default=None,
        )
    return {
        "active_mode": user.active_mode,
        "recent_mode": recent_mode,
        "recently_used_mode": recent_mode,
        "total_modes": len(user.modes),
        "modes": sorted(user.modes, key=lambda mode: (not mode.is_active, mode.created_at)),
        "metrics": metrics,
        "weekly_activity": weekly_activity,
        "connected_device": connected_device,
    }


def analytics_data(user: User) -> dict:
    stats = DailyUsageStat.query.filter_by(user_id=user.id).all()
    relevant = sum(stat.relevant_count for stat in stats)
    filtered = sum(stat.filtered_count for stat in stats)
    seconds = sum(stat.estimated_seconds_saved for stat in stats)
    total = relevant + filtered

    categories = Counter()
    for mode in user.modes:
        categories.update(mode.preferred_categories or [])

    dashboard = dashboard_data(user)
    top_counts = categories.most_common(5)
    category_total = sum(count for _, count in top_counts)
    dashboard.update(
        {
            "metrics": {
                "relevant": relevant,
                "relevant_shorts": relevant,
                "filtered": filtered,
                "filtered_shorts": filtered,
                "time_saved_seconds": seconds,
                "time_saved_label": format_duration(seconds),
                "learning_accuracy": round((relevant / total) * 100) if total else 0,
                "learning_accuracy_label": f"{round((relevant / total) * 100)}%"
                if total
                else "—",
            },
            "top_categories": [
                {
                    "name": name,
                    "count": count,
                    "percent": round((count / category_total) * 100)
                    if category_total
                    else 0,
                }
                for name, count in top_counts
            ],
            "weekly_stats": dashboard["weekly_activity"],
        }
    )
    return dashboard

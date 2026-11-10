import asyncio
import copy
import json
import math
import re
import time

try:
    from .scene_profiles import slot_analysis, policy
    from .profile_core import normalize_quote, quote_scene
except ImportError:
    from scene_profiles import slot_analysis, policy
    from profile_core import normalize_quote, quote_scene


def validate(report):
    if not isinstance(report, dict) or report.get("schema_version") != 1:
        raise ValueError("invalid report")
    if (report.get("source") != "astrbot_plugin_insult_guard"
            or report.get("verification") != "independent_double_review"
            or report.get("scene") != "group"):
        raise ValueError("unsupported report")
    for key in ("qq", "self_id", "group_id"):
        if not isinstance(report.get(key), str) or not re.fullmatch(r"\d{5,12}", report[key]):
            raise ValueError("invalid identity")
    for key in ("platform_id", "incident_id", "kind"):
        if not isinstance(report.get(key), str) or not 1 <= len(report[key]) <= 128:
            raise ValueError("invalid source")
    evidence = report.get("evidence")
    if not isinstance(evidence, list) or not 1 <= len(evidence) <= 5:
        raise ValueError("invalid evidence")
    for item in evidence:
        if not isinstance(item, dict) or not isinstance(item.get("message_id"), str) or not item["message_id"].isdigit():
            raise ValueError("invalid message")
        if not isinstance(item.get("text"), str) or not 1 <= len(item["text"]) <= 600:
            raise ValueError("invalid text")
        stamp = item.get("time")
        if type(stamp) not in (int, float) or not math.isfinite(stamp) or not 0 <= time.time() - stamp <= 600:
            raise ValueError("stale evidence")
        if type(item.get("severity")) is not int or not 1 <= item["severity"] <= 3:
            raise ValueError("invalid severity")
        if not isinstance(item.get("reason"), str) or len(item["reason"]) > 500:
            raise ValueError("invalid reason")
        context = item.get("context")
        if not isinstance(context, list) or len(context) > 6:
            raise ValueError("invalid context")
        for entry in context:
            if (not isinstance(entry, dict) or not isinstance(entry.get("text"), str)
                    or len(entry["text"]) > 600 or not isinstance(entry.get("sender_id"), str)
                    or not entry["sender_id"].isdigit()):
                raise ValueError("invalid context author")
    if len(json.dumps(report, ensure_ascii=False)) > 30000:
        raise ValueError("oversized report")
    return copy.deepcopy(report)


class ExternalObservationMixin:
    async def request_external_observation(self, report):
        if not self._enabled() or self.config.get("external_observation_enable", False) is not True:
            return {"status": "disabled"}
        try:
            report = validate(report)
        except (ValueError, TypeError, OverflowError):
            return {"status": "invalid_report"}
        qq = report["qq"]
        if not self._group_allowed(report["group_id"]) or not self.config.get("store_quotes", True):
            return {"status": "collection_disabled"}
        generation = self._user_generation(qq)
        await self._ensure_loaded()
        if not hasattr(self, "_external_observation_queue"):
            self._external_observation_queue = asyncio.Queue(maxsize=32)
            self._external_observation_task = None
        async with self._store_lock:
            if generation != self._user_generation(qq):
                return {"status": "deleted"}
            if qq not in self._stats and len(self._stats) >= self._max_tracked():
                return {"status": "capacity"}
            previous = self._stats.get(qq)
            stat = copy.deepcopy(previous or {})
            now = time.time()
            retention = self.config.get("quote_retention_days", 30)
            records = [item for item in stat.get("external_observations", [])
                       if not retention or now - item.get("received_at", 0) <= retention * 86400]
            key = [report[name] for name in ("platform_id", "self_id", "group_id", "incident_id")]
            if any(item.get("key") == key for item in records):
                return {"status": "duplicate"}
            records.append({"key": key, "received_at": now, "report": report})
            stat["external_observations"] = records[-20:]
            cooldown = self.config.get("external_observation_cooldown", 300)
            if type(cooldown) is not int or not 30 <= cooldown <= 86400:
                cooldown = 300
            status = ("recorded_analysis_disabled" if not self.config.get("llm_tags", True)
                      else "recorded_cooldown" if now - stat.get("external_refresh_requested_at", 0) < cooldown
                      else "recorded_queue_full" if self._external_observation_queue.full() else "queued")
            if status == "queued":
                stat["external_refresh_requested_at"] = now
            self._stats[qq] = stat
            try:
                await self.put_kv_data("up_stats", self._stats)
            except BaseException:
                if previous is None:
                    self._stats.pop(qq, None)
                else:
                    self._stats[qq] = previous
                raise
            if status != "queued":
                return {"status": status}
            self._external_observation_queue.put_nowait((qq, generation))
        if self._external_observation_task is None or self._external_observation_task.done():
            self._external_observation_task = asyncio.create_task(self._external_observation_worker())
        self._ensure_flush_task()
        return {"status": "queued"}

    def _external_group_materials(self, stat, quotes):
        if self.config.get("external_observation_enable", False) is not True or not self.config.get("store_quotes", True):
            return quotes
        quotes = copy.deepcopy(quotes)
        retention = self.config.get("quote_retention_days", 30)
        for record in stat.get("external_observations", []):
            report = record["report"]
            if (not self._group_allowed(report["group_id"])
                    or (retention and time.time() - record["received_at"] > retention * 86400)):
                continue
            for item in report["evidence"]:
                quotes = [q for q in quotes if not (q.get("text") == item["text"] and q.get("group_id") == report["group_id"])]
                quotes.append(normalize_quote({"text": item["text"], "t": int(item["time"]),
                    "src": "群 " + report["group_id"], "group_id": report["group_id"],
                    "external_context": item["context"], "external_observation": True}))
        return sorted(quotes, key=lambda item: item.get("t", 0))[-20:]

    async def _external_observation_worker(self):
        while True:
            qq, generation = await self._external_observation_queue.get()
            try:
                if (not self._enabled() or self.config.get("external_observation_enable", False) is not True
                        or not self.config.get("llm_tags", True) or generation != self._user_generation(qq)):
                    continue
                async with self._store_lock:
                    stat = copy.deepcopy(self._stats.get(qq) or {})
                    quotes = copy.deepcopy(self._quotes.get(qq) or [])
                retention = self.config.get("quote_retention_days", 30)
                quotes = [item for item in quotes if quote_scene(item) == "group"
                          and (not retention or time.time() - item.get("t", 0) <= retention * 86400)]
                quotes = self._external_group_materials(stat, quotes)
                if quotes and self.config.get("store_quotes", True):
                    stats = {"g_count": max(int(stat.get("g_count") or 0), int(stat.get("platform_history_count") or 0))}
                    await slot_analysis(self, qq, "group", quotes[-20:], stats, [], policy(self), generation, force_refresh=True)
            except asyncio.CancelledError:
                raise
            except Exception:
                self._llm_status[qq] = "error"
            finally:
                self._external_observation_queue.task_done()

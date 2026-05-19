"""Reconstruct the user's userdata.json after PowerShell wiped it.

The original UTF-8 file was misread by PowerShell as cp949, mangling
Korean labels. I have the structural values from the failed dump in
context — this script rewrites them with placeholder labels for the
Korean strings I can't safely reverse-decode.
"""
import json, sys
from pathlib import Path

OUT = Path.home() / "AppData" / "Local" / "QuickCast" / "userdata.json"

data = {
  "arduino_port": "COM3",
  "arduino_baud": 9600,
  "telegram_token": "",
  "telegram_chat_id": "",
  "master_switch": False,
  "sura_mode": False,
  "hp_cap": {"x": 78, "y": 68},
  "hp_cap_w": 160, "hp_cap_h": 14,
  "mp_cap": {"x": 76, "y": 100},
  "mp_cap_w": 157, "mp_cap_h": 17,
  "hp_text_cap": {"x": 88, "y": 18},
  "hp_text_cap_w": 136, "hp_text_cap_h": 16,
  "mp_text_cap": {"x": 88, "y": 32},
  "mp_text_cap_w": 137, "mp_text_cap_h": 11,
  "potion_text_cap": {"x": 479, "y": 665},
  "potion_text_cap_w": 33, "potion_text_cap_h": 25,
  "ocr_mode": False,
  "ocr_threshold": 144,
  "slots": {
    "1": {"label": "슬롯1", "use": True,  "hp": {"min": 0,  "max": 10},  "mp": {"min": 0,  "max": 100}, "key": "0",  "count": 3, "delay": 0.2, "cooltime": 5.0,   "repeat": True, "tele_use": False},
    "2": {"label": "슬롯2", "use": False, "hp": {"min": 0,  "max": 75},  "mp": {"min": 10, "max": 100}, "key": "`",  "count": 3, "delay": 0.2, "cooltime": 13.0,  "repeat": True, "tele_use": False},
    "3": {"label": "슬롯3", "use": False, "hp": {"min": 0,  "max": 85},  "mp": {"min": 10, "max": 100}, "key": "1",  "count": 3, "delay": 0.2, "cooltime": 5.0,   "repeat": True, "tele_use": False},
    "4": {"label": "MP포션", "use": False, "hp": {"min": 0, "max": 100}, "mp": {"min": 0,  "max": 95},  "key": "f8", "count": 3, "delay": 0.2, "cooltime": 300.0, "repeat": True, "tele_use": False},
    "5": {"label": "슬롯5", "use": False, "hp": {"min": 0,  "max": 100}, "mp": {"min": 0,  "max": 100}, "key": "f7", "count": 3, "delay": 0.2, "cooltime": 301.0, "repeat": True, "tele_use": False},
    "6": {"label": "슬롯6", "use": False, "hp": {"min": 0,  "max": 65},  "mp": {"min": 0,  "max": 100}, "key": "f9", "count": 3, "delay": 0.2, "cooltime": 901.0, "repeat": True, "tele_use": False}
  },
  "pk": {
    "use": True, "hp": {"min": 0, "max": 60}, "key": "0", "count": 3,
    "delay": 0.2, "cooltime": 3.0, "repeat": True,
    "cap": {"x": 1089, "y": 561}, "cap_w": 25, "cap_h": 25,
    "threshold": 3050000
  },
  "potion": {
    "use": True, "hp": {"min": 0, "max": 30}, "key": "0", "count": 3,
    "delay": 0.2,
    "cap": {"x": 503, "y": 646}, "cap_w": 13, "cap_h": 13,
    "threshold": 110000
  },
  "alarms": [
    {"id": "3dc511bb", "label": "알람1", "hour": 20, "minute": 55, "enabled": False, "repeat_minutes": 2, "days": [1, 2], "mode": "repeat"},
    {"id": "c68dfc76", "label": "알람2", "hour": 20, "minute": 55, "enabled": False, "repeat_minutes": 2, "days": [0],    "mode": "repeat"},
    {"id": "38816807", "label": "알람3", "hour": 20, "minute": 55, "enabled": False, "repeat_minutes": 2, "days": [6],    "mode": "repeat"}
  ],
  "alarm_popup_enabled": True,
  "alarm_auto_close_minutes": 5,
  "alarm_repeat_minutes": 0,
  "alarm_sound": "off",
  "alarm_sound_volume": 80,
  "capture_fps": 10,
  "theme": "graphite",
  "game_window_patterns": ["리니지W", "Lineage W", "LINEAGE", "퍼플", "PURPLE"],
  "capture_window_title": "리니지 리마스터 - Lineage Remaster - Chrome",
  "capture_monitor_index": 1,
  "roi_locked": False,
  "roi_profiles": {
    "5:4":  {"hp_cap": {"x": 78, "y": 24}, "hp_cap_w": 160, "hp_cap_h": 5,  "mp_cap": {"x": 76, "y": 35},  "mp_cap_w": 157, "mp_cap_h": 6,  "pk_cap": {"x": 1089, "y": 561}, "pk_cap_w": 25, "pk_cap_h": 25, "potion_cap": {"x": 503, "y": 646}, "potion_cap_w": 13, "potion_cap_h": 13},
    "3:2":  {"hp_cap": {"x": 78, "y": 29}, "hp_cap_w": 160, "hp_cap_h": 6,  "mp_cap": {"x": 76, "y": 42},  "mp_cap_w": 157, "mp_cap_h": 7,  "pk_cap": {"x": 1097, "y": 566}, "pk_cap_w": 25, "pk_cap_h": 25, "potion_cap": {"x": 503, "y": 775}, "potion_cap_w": 13, "potion_cap_h": 13},
    "16:9": {"hp_cap": {"x": 78, "y": 24}, "hp_cap_w": 160, "hp_cap_h": 5,  "mp_cap": {"x": 76, "y": 35},  "mp_cap_w": 157, "mp_cap_h": 6,  "pk_cap": {"x": 1085, "y": 545}, "pk_cap_w": 49, "pk_cap_h": 44, "potion_cap": {"x": 491, "y": 638}, "potion_cap_w": 35, "potion_cap_h": 29},
    "32:9": {"hp_cap": {"x": 78, "y": 68}, "hp_cap_w": 160, "hp_cap_h": 14, "mp_cap": {"x": 76, "y": 100}, "mp_cap_w": 157, "mp_cap_h": 17, "pk_cap": {"x": 1089, "y": 561}, "pk_cap_w": 25, "pk_cap_h": 25, "potion_cap": {"x": 503, "y": 646}, "potion_cap_w": 13, "potion_cap_h": 13}
  },
  "active_aspect": "32:9",
  "recovery": {
    "enabled": True,
    "trigger_potion": True, "trigger_pk": True, "trigger_hp_zero": False,
    "trigger_slot_ids": ["1"],
    "start_delay_seconds": 30,
    "cooldown_seconds": 60,
    "steps": [
      {"label": "복귀1", "x": 640, "y": 360, "key": "f12",  "delay_after_ms": 10000},
      {"label": "복귀2", "x": 660, "y": 380, "key": "e",    "delay_after_ms": 3000},
      {"label": "복귀3", "x": 680, "y": 400, "key": "num2", "delay_after_ms": 500}
    ]
  },
  "item_close": {
    "enabled": True, "x": 53, "y": 709, "interval_seconds": 300.0
  },
  "overlay_closes": {
    "pet_whistle":   {"enabled": True,  "cap": {"x": 591, "y": 100}, "cap_w": 114, "cap_h": 93, "threshold": 3000000, "close_key": "esc", "cooldown_seconds": 2.0},
    "item_acquired": {"enabled": False, "cap": {"x": 591, "y": 100}, "cap_w": 114, "cap_h": 93, "threshold": 3000000, "close_key": "esc", "cooldown_seconds": 2.0}
  },
  "notify_on_alarm_toast": True,
  "notify_on_action_toast": False,
  "input_backend": "postmessage",
  "floater_enabled": True,
  "tutorial_completed": True
}

OUT.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
print(f"restored {OUT} ({OUT.stat().st_size} bytes)")

"""
LMSuite — LM Studio Multi-Model Orchestrator
Single-file build with the Constraint Engine and the Prompt Editor merged in directly
(no cross-file imports: constraint_engine.py and lm_prompt_editor.py are both built in)
"""

import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import requests
import os
import threading
import datetime
import sys
import subprocess
import psutil
import time
import re
import json
import difflib
import glob
import shutil
import string
from abc import ABC, abstractmethod
from typing import Any, List, Dict, Union


# ============================================================================
# CONSTRAINT ENGINE — SEPARATE CONSTRAINTS (Local, Fast, Per-Item)
# ============================================================================

class Constraint(ABC):
    """Base constraint - all constraints inherit from this"""

    def __init__(self, name: str):
        self.name = name
        self.passed = False
        self.execution_time = 0

    @abstractmethod
    def validate(self, data: Any) -> bool:
        """Does data pass this constraint?"""
        pass

    @abstractmethod
    def fix(self, data: Any) -> Any:
        """Fix data if it fails"""
        pass

    def describe(self):
        """Human-readable description"""
        return f"{self.name}"


class SeparateConstraint(Constraint):
    """Validates individual pieces in isolation"""
    pass


class NoDoubleSpaces(SeparateConstraint):
    """Remove multiple spaces"""

    def validate(self, data: str) -> bool:
        return "  " not in data

    def fix(self, data: str) -> str:
        return re.sub(r"  +", " ", data)


class CapitalizeFirst(SeparateConstraint):
    """First letter must be capitalized"""

    def validate(self, data: str) -> bool:
        if not data:
            return True
        return data[0].isupper()

    def fix(self, data: str) -> str:
        # A leading space/newline (common in local-model output) used to make this a no-op:
        # ' '.upper() is still ' ', so validate() failed again right after "fixing" it and the
        # whole round got discarded. Strip leading whitespace before capitalizing so the fix
        # actually fixes it.
        if not data:
            return data
        stripped = data.lstrip()
        if not stripped:
            return data
        return stripped[0].upper() + stripped[1:]


class ValidJSON(SeparateConstraint):
    """Must be valid JSON"""

    def __init__(self, name: str = "ValidJSON", required_fields: List[str] = None):
        super().__init__(name)
        self.required_fields = required_fields or []

    def validate(self, data: Union[str, dict]) -> bool:
        try:
            if isinstance(data, str):
                parsed = json.loads(data)
            else:
                parsed = data

            if isinstance(parsed, dict):
                for field in self.required_fields:
                    if field not in parsed:
                        return False

            return True
        except Exception:
            return False

    def fix(self, data: str) -> str:
        return data


class NoTrailingWhitespace(SeparateConstraint):
    """Remove trailing spaces"""

    def validate(self, data: str) -> bool:
        if isinstance(data, str):
            return not data.endswith((" ", "\n", "\t"))
        return True

    def fix(self, data: str) -> str:
        if isinstance(data, str):
            return data.rstrip()
        return data


class IsNonEmpty(SeparateConstraint):
    """Data must not be empty"""

    def validate(self, data: Any) -> bool:
        if isinstance(data, (str, list, dict)):
            return len(data) > 0
        return data is not None

    def fix(self, data: Any) -> Any:
        return data


class MaxLength(SeparateConstraint):
    """Enforce maximum length"""

    def __init__(self, name: str = "MaxLength", max_len: int = 1000):
        super().__init__(name)
        self.max_len = max_len

    def validate(self, data: str) -> bool:
        return len(data) <= self.max_len

    def fix(self, data: str) -> str:
        return data[:self.max_len]


class ContainsKeyword(SeparateConstraint):
    """Must contain specific keyword"""

    def __init__(self, name: str = "ContainsKeyword", keyword: str = ""):
        super().__init__(name)
        self.keyword = keyword

    def validate(self, data: str) -> bool:
        return self.keyword.lower() in data.lower()

    def fix(self, data: str) -> str:
        return data


class NoCharacters(SeparateConstraint):
    """Ban specific characters"""

    def __init__(self, name: str = "NoCharacters", banned_chars: str = ""):
        super().__init__(name)
        self.banned_chars = banned_chars

    def validate(self, data: str) -> bool:
        return not any(char in data for char in self.banned_chars)

    def fix(self, data: str) -> str:
        for char in self.banned_chars:
            data = data.replace(char, "")
        return data


class NoMarkdown(SeparateConstraint):
    """Strip markdown formatting"""

    def validate(self, data: str) -> bool:
        markdown_chars = ['#', '*', '_', '`', '[', ']', '!', '~']
        return not any(char in data for char in markdown_chars)

    def fix(self, data: str) -> str:
        markdown_chars = ['#', '*', '_', '`', '[', ']', '!', '~']
        for char in markdown_chars:
            data = data.replace(char, "")
        return data


class IsEnglish(SeparateConstraint):
    """Ensure content is primarily English"""

    def __init__(self, name: str = "IsEnglish", min_english_ratio: float = 0.85):
        super().__init__(name)
        self.min_english_ratio = min_english_ratio

    def validate(self, data: str) -> bool:
        if not data:
            return True

        english_chars = sum(1 for c in data if ord(c) < 128 and (c.isalpha() or c.isdigit() or c in " .,!?;:-'\"()"))
        ratio = english_chars / len(data)
        return ratio >= self.min_english_ratio

    def fix(self, data: str) -> str:
        return "".join(c for c in data if ord(c) < 128)


# ============================================================================
# CONSTRAINT ENGINE — COLLECTIVE CONSTRAINTS (Global, Complex, Whole-Output)
# ============================================================================

class CollectiveConstraint(Constraint):
    """Validates entire output or relationships between pieces"""
    pass


class NoContradictions(CollectiveConstraint):
    """Output must not contradict itself"""

    def validate(self, data: Union[str, dict]) -> bool:
        if isinstance(data, dict):
            data = json.dumps(data)

        contradictions = [
            ("no fever", "high fever"),
            ("not", "must"),
            ("false", "true"),
        ]

        data_lower = data.lower()
        for neg, pos in contradictions:
            if neg in data_lower and pos in data_lower:
                return False

        return True

    def fix(self, data: Any) -> Any:
        return data


class JSONFieldsPopulated(CollectiveConstraint):
    """All required JSON fields must have content"""

    def __init__(self, name: str = "JSONFieldsPopulated", required_fields: List[str] = None):
        super().__init__(name)
        self.required_fields = required_fields or []

    def validate(self, data: Union[str, dict]) -> bool:
        try:
            if isinstance(data, str):
                parsed = json.loads(data)
            else:
                parsed = data

            for field in self.required_fields:
                if field not in parsed or not parsed[field]:
                    return False

            return True
        except Exception:
            return False

    def fix(self, data: Any) -> Any:
        return data


class LogicalFlow(CollectiveConstraint):
    """Output must follow logical progression"""

    def __init__(self, name: str = "LogicalFlow", required_order: List[str] = None):
        super().__init__(name)
        self.required_order = required_order or []

    def validate(self, data: str) -> bool:
        if isinstance(data, dict):
            data = json.dumps(data)

        data_lower = data.lower()
        last_pos = -1

        for keyword in self.required_order:
            pos = data_lower.find(keyword.lower())
            if pos == -1:
                return False
            if pos <= last_pos:
                return False
            last_pos = pos

        return True

    def fix(self, data: Any) -> Any:
        return data


class NoRepetition(CollectiveConstraint):
    """Detect and penalize repetitive n-grams"""

    def __init__(self, name: str = "NoRepetition", ngram_size: int = 3):
        super().__init__(name)
        self.ngram_size = ngram_size

    def validate(self, data: str) -> bool:
        if isinstance(data, dict):
            data = json.dumps(data)

        words = data.lower().split()
        if len(words) < self.ngram_size * 2:
            return True

        ngrams = []
        for i in range(len(words) - self.ngram_size + 1):
            ngram = tuple(words[i:i + self.ngram_size])
            ngrams.append(ngram)

        return len(ngrams) == len(set(ngrams))

    def fix(self, data: str) -> str:
        return data


class FactualGrounding(CollectiveConstraint):
    """Encourage factual statements"""

    def __init__(self, name: str = "FactualGrounding", disallow_words: List[str] = None):
        super().__init__(name)
        self.disallow_words = disallow_words or []

    def validate(self, data: str) -> bool:
        if isinstance(data, dict):
            data = json.dumps(data)

        data_lower = data.lower()
        for word in self.disallow_words:
            if word.lower() in data_lower:
                return False
        return True

    def fix(self, data: str) -> str:
        result = data
        for word in self.disallow_words:
            result = re.sub(r'\b' + re.escape(word) + r'\b', '', result, flags=re.IGNORECASE)
        return result


class MinDistinctSentences(CollectiveConstraint):
    """Require at least N genuinely distinct (non-paraphrased) sentences. NoRepetition
    catches exact repeated n-grams; this catches padding via reworded restatements of the
    same sentence, using fuzzy sentence-to-sentence similarity instead of fixed n-grams."""

    def __init__(self, name: str = "MinDistinctSentences", min_sentences: int = 2,
                 similarity_threshold: float = 0.85):
        super().__init__(name)
        self.min_sentences = min_sentences
        self.similarity_threshold = similarity_threshold

    def validate(self, data: Union[str, dict]) -> bool:
        if isinstance(data, dict):
            data = json.dumps(data)

        sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+', data) if s.strip()]
        distinct = []
        for s in sentences:
            if not any(difflib.SequenceMatcher(None, s.lower(), d.lower()).ratio() >= self.similarity_threshold
                       for d in distinct):
                distinct.append(s)

        return len(distinct) >= self.min_sentences

    def fix(self, data: Any) -> Any:
        return data


class StyleConsistency(CollectiveConstraint):
    """Enforce style consistency across output"""

    def __init__(self, name: str = "StyleConsistency", enforce_style: str = "formal"):
        super().__init__(name)
        self.enforce_style = enforce_style

    def validate(self, data: str) -> bool:
        if isinstance(data, dict):
            data = json.dumps(data)

        if self.enforce_style == "formal":
            contractions = ["can't", "won't", "don't", "isn't", "aren't", "wasn't", "weren't"]
            for contraction in contractions:
                if contraction.lower() in data.lower():
                    return False

        return True

    def fix(self, data: str) -> str:
        if self.enforce_style == "formal":
            replacements = {
                "can't": "cannot", "won't": "will not", "don't": "do not",
                "doesn't": "does not", "didn't": "did not", "isn't": "is not",
                "aren't": "are not", "wasn't": "was not", "weren't": "were not",
                "haven't": "have not", "hasn't": "has not", "hadn't": "had not",
            }
            result = data
            for contraction, expansion in replacements.items():
                result = re.sub(r'\b' + re.escape(contraction) + r'\b', expansion, result, flags=re.IGNORECASE)
            return result
        return data


class MinLength(SeparateConstraint):
    """Enforce a minimum character length — catches truncated or lazy one-line outputs
    that MaxLength can't (MaxLength only guards the ceiling, not the floor)."""

    def __init__(self, name: str = "MinLength", min_len: int = 20):
        super().__init__(name)
        self.min_len = min_len

    def validate(self, data: str) -> bool:
        if not isinstance(data, str):
            return True
        return len(data) >= self.min_len

    def fix(self, data: str) -> str:
        # Missing content can't be safely fabricated — leave validate() failing so the
        # caller's normal reject/retry path handles it instead of a fake "fix".
        return data


class NoURLs(SeparateConstraint):
    """Strip or reject URLs — most useful on offline/self-knowledge runs, where a model
    citing a link it can't actually have accessed is a fabricated citation, not a source."""

    URL_PATTERN = re.compile(r'(https?://\S+|www\.\S+)', re.IGNORECASE)

    def validate(self, data: str) -> bool:
        if not isinstance(data, str):
            return True
        return not self.URL_PATTERN.search(data)

    def fix(self, data: str) -> str:
        if not isinstance(data, str):
            return data
        return self.URL_PATTERN.sub("", data).strip()


class NoPlaceholderText(SeparateConstraint):
    """Catch unresolved template placeholders/boilerplate left in by the model
    (e.g. '[insert X here]', 'TODO', 'Lorem ipsum', '<your answer>')."""

    PLACEHOLDER_PATTERNS = [
        r'\[insert[^\]]*\]', r'\[todo[^\]]*\]', r'\btodo\b', r'\btbd\b',
        r'lorem ipsum', r'<[a-zA-Z_ ]+>', r'\[your [a-zA-Z ]+\]', r'\[x+\]',
    ]

    def validate(self, data: str) -> bool:
        if not isinstance(data, str):
            return True
        data_lower = data.lower()
        return not any(re.search(p, data_lower) for p in self.PLACEHOLDER_PATTERNS)

    def fix(self, data: str) -> str:
        # No safe auto-fix — the content behind the placeholder is genuinely missing.
        return data


class EndsWithPunctuation(SeparateConstraint):
    """Output must end with proper terminal punctuation — a cheap, reliable signal that a
    generation got cut off mid-sentence (context limit, stop token misfire, etc.)."""

    def __init__(self, name: str = "EndsWithPunctuation", allowed: str = ".!?\"')"):
        super().__init__(name)
        self.allowed = allowed

    def validate(self, data: str) -> bool:
        if not isinstance(data, str) or not data.strip():
            return True
        return data.rstrip()[-1] in self.allowed

    def fix(self, data: str) -> str:
        if not isinstance(data, str):
            return data
        stripped = data.rstrip()
        if not stripped:
            return data
        if stripped[-1] not in self.allowed:
            return stripped + "."
        return stripped


class NoRefusalLanguage(SeparateConstraint):
    """Flag model refusals/deflections ('As an AI...', 'I cannot help with that') so a
    refusal doesn't get silently absorbed into a cascade or knowledge base as real content."""

    REFUSAL_PHRASES = [
        "as an ai", "as a language model", "i cannot help with", "i can't help with",
        "i cannot fulfill", "i can't fulfill", "i'm not able to help", "i am not able to help",
        "i cannot provide", "i can't provide", "i'm sorry, but i", "i'm unable to",
    ]

    def validate(self, data: str) -> bool:
        if not isinstance(data, str):
            return True
        data_lower = data.lower()
        return not any(phrase in data_lower for phrase in self.REFUSAL_PHRASES)

    def fix(self, data: str) -> str:
        # No safe auto-fix — a refusal has to be re-generated, not text-patched.
        return data


# ============================================================================
# CONSTRAINT ENGINE — ORCHESTRATOR
# ============================================================================

class ConstraintEngine:
    """Main engine that applies constraints"""

    def __init__(self, name: str = "ConstraintEngine"):
        self.name = name
        self.separate_constraints: List[SeparateConstraint] = []
        self.collective_constraints: List[CollectiveConstraint] = []
        self.execution_log: List[Dict] = []

    def add_separate(self, constraint: SeparateConstraint):
        self.separate_constraints.append(constraint)

    def add_collective(self, constraint: CollectiveConstraint):
        self.collective_constraints.append(constraint)

    def validate_separate(self, data: Any) -> Dict:
        results = []
        for constraint in self.separate_constraints:
            start = time.time()
            passed = constraint.validate(data)
            duration = (time.time() - start) * 1000
            results.append({"constraint": constraint.name, "passed": passed, "duration_ms": duration})
            if not passed:
                return {"success": False, "failed_at": constraint.name, "results": results}
        return {"success": True, "results": results}

    def validate_collective(self, data: Any) -> Dict:
        results = []
        for constraint in self.collective_constraints:
            start = time.time()
            passed = constraint.validate(data)
            duration = (time.time() - start) * 1000
            results.append({"constraint": constraint.name, "passed": passed, "duration_ms": duration})
            if not passed:
                return {"success": False, "failed_at": constraint.name, "results": results}
        return {"success": True, "results": results}

    def validate(self, data: Any, auto_fix: bool = False) -> Dict:
        """Run all constraints (separate then collective)"""
        current = data
        log = []

        for constraint in self.separate_constraints:
            start = time.time()
            passed = constraint.validate(current)
            duration = (time.time() - start) * 1000
            log.append({"phase": "separate", "constraint": constraint.name, "passed": passed, "duration_ms": duration})

            if not passed:
                if auto_fix:
                    current = constraint.fix(current)
                    fixed_passed = constraint.validate(current)
                    if fixed_passed:
                        log[-1]["fixed"] = True
                    else:
                        return {"success": False, "failed_at": constraint.name, "phase": "separate", "log": log}
                else:
                    return {"success": False, "failed_at": constraint.name, "phase": "separate", "log": log}

        if len(self.collective_constraints) > 0:
            for constraint in self.collective_constraints:
                start = time.time()
                passed = constraint.validate(current)
                duration = (time.time() - start) * 1000
                log.append({"phase": "collective", "constraint": constraint.name, "passed": passed, "duration_ms": duration})

                if not passed:
                    if auto_fix:
                        current = constraint.fix(current)
                        fixed_passed = constraint.validate(current)
                        if fixed_passed:
                            log[-1]["fixed"] = True
                        else:
                            return {"success": False, "failed_at": constraint.name, "phase": "collective", "log": log}
                    else:
                        return {"success": False, "failed_at": constraint.name, "phase": "collective", "log": log}

        self.execution_log = log
        return {"success": True, "data": current, "log": log}

    def describe(self) -> Dict:
        return {
            "separate": [c.name for c in self.separate_constraints],
            "collective": [c.name for c in self.collective_constraints]
        }


# ============================================================================
# CONSTRAINT REGISTRY - Maps type names to classes and their editable params
# ============================================================================

CONSTRAINT_REGISTRY = {
    "NoDoubleSpaces": {
        "class": NoDoubleSpaces, "category": "separate",
        "description": "Collapse multiple spaces into one", "auto_fix": True, "params": {}
    },
    "CapitalizeFirst": {
        "class": CapitalizeFirst, "category": "separate",
        "description": "First character must be uppercase", "auto_fix": True, "params": {}
    },
    "NoTrailingWhitespace": {
        "class": NoTrailingWhitespace, "category": "separate",
        "description": "Strip trailing spaces, newlines, tabs", "auto_fix": True, "params": {}
    },
    "IsNonEmpty": {
        "class": IsNonEmpty, "category": "separate",
        "description": "Reject empty or None data", "auto_fix": False, "params": {}
    },
    "MaxLength": {
        "class": MaxLength, "category": "separate",
        "description": "Enforce maximum character length", "auto_fix": True,
        "params": {"max_len": {"type": "int", "default": 10000, "label": "Max Characters", "min": 1, "max": 100000}}
    },
    "ContainsKeyword": {
        "class": ContainsKeyword, "category": "separate",
        "description": "Output must contain a specific keyword", "auto_fix": False,
        "params": {"keyword": {"type": "str", "default": "", "label": "Required Keyword"}}
    },
    "NoCharacters": {
        "class": NoCharacters, "category": "separate",
        "description": "Ban specific characters from output", "auto_fix": True,
        "params": {"banned_chars": {"type": "str", "default": "", "label": "Banned Characters"}}
    },
    "ValidJSON": {
        "class": ValidJSON, "category": "separate",
        "description": "Must be valid JSON (optional required fields)", "auto_fix": False,
        "params": {"required_fields": {"type": "list", "default": [], "label": "Required Fields (comma-separated)"}}
    },
    "NoMarkdown": {
        "class": NoMarkdown, "category": "separate",
        "description": "Strip markdown formatting characters", "auto_fix": True, "params": {}
    },
    "IsEnglish": {
        "class": IsEnglish, "category": "separate",
        "description": "Ensure content is primarily English", "auto_fix": True,
        "params": {"min_english_ratio": {"type": "float", "default": 0.85, "label": "Min English Ratio (0.0-1.0)"}}
    },
    "NoContradictions": {
        "class": NoContradictions, "category": "collective",
        "description": "Detect contradictory statements", "auto_fix": False, "params": {}
    },
    "JSONFieldsPopulated": {
        "class": JSONFieldsPopulated, "category": "collective",
        "description": "All required JSON fields must have content", "auto_fix": False,
        "params": {"required_fields": {"type": "list", "default": [], "label": "Required Fields (comma-separated)"}}
    },
    "LogicalFlow": {
        "class": LogicalFlow, "category": "collective",
        "description": "Keywords must appear in a specific order", "auto_fix": False,
        "params": {"required_order": {"type": "list", "default": [], "label": "Keywords in Order (comma-separated)"}}
    },
    "NoRepetition": {
        "class": NoRepetition, "category": "collective",
        "description": "Detect and penalize repetitive n-grams", "auto_fix": False,
        "params": {"ngram_size": {"type": "int", "default": 3, "label": "N-gram Size", "min": 2, "max": 10}}
    },
    "FactualGrounding": {
        "class": FactualGrounding, "category": "collective",
        "description": "Encourage factual statements", "auto_fix": True,
        "params": {"disallow_words": {"type": "list", "default": [], "label": "Disallow Words (comma-separated)"}}
    },
    "StyleConsistency": {
        "class": StyleConsistency, "category": "collective",
        "description": "Enforce style consistency across output", "auto_fix": True,
        "params": {"enforce_style": {"type": "str", "default": "formal", "label": "Style (formal/casual)"}}
    },
    "MinLength": {
        "class": MinLength, "category": "separate",
        "description": "Enforce a minimum character length (catches truncated/lazy outputs)", "auto_fix": False,
        "params": {"min_len": {"type": "int", "default": 20, "label": "Min Characters", "min": 1, "max": 100000}}
    },
    "NoURLs": {
        "class": NoURLs, "category": "separate",
        "description": "Ban URLs from the output (useful for offline/self-knowledge runs)", "auto_fix": True, "params": {}
    },
    "NoPlaceholderText": {
        "class": NoPlaceholderText, "category": "separate",
        "description": "Reject unresolved template placeholders (TODO, [insert X], Lorem ipsum, <..>)",
        "auto_fix": False, "params": {}
    },
    "EndsWithPunctuation": {
        "class": EndsWithPunctuation, "category": "separate",
        "description": "Output must end in proper terminal punctuation (catches mid-sentence truncation)",
        "auto_fix": True, "params": {}
    },
    "NoRefusalLanguage": {
        "class": NoRefusalLanguage, "category": "separate",
        "description": "Flag model refusals/deflections instead of treating them as real content",
        "auto_fix": False, "params": {}
    },
    "MinDistinctSentences": {
        "class": MinDistinctSentences, "category": "collective",
        "description": "Require at least N genuinely distinct (non-paraphrased) sentences", "auto_fix": False,
        "params": {
            "min_sentences": {"type": "int", "default": 2, "label": "Min Distinct Sentences", "min": 1, "max": 50},
            "similarity_threshold": {"type": "float", "default": 0.85, "label": "Similarity Threshold (0.0-1.0)"}
        }
    },
}


# ============================================================================
# SERIALIZATION - Save/load constraint configs to JSON
# ============================================================================

def engine_to_config(engine: ConstraintEngine) -> dict:
    """Serialize a ConstraintEngine to a saveable dict"""
    config = {"name": engine.name, "separate": [], "collective": []}

    for c in engine.separate_constraints:
        entry = {"type": type(c).__name__, "name": c.name}
        reg = CONSTRAINT_REGISTRY.get(type(c).__name__, {})
        for param_name in reg.get("params", {}):
            entry[param_name] = getattr(c, param_name, reg["params"][param_name]["default"])
        config["separate"].append(entry)

    for c in engine.collective_constraints:
        entry = {"type": type(c).__name__, "name": c.name}
        reg = CONSTRAINT_REGISTRY.get(type(c).__name__, {})
        for param_name in reg.get("params", {}):
            entry[param_name] = getattr(c, param_name, reg["params"][param_name]["default"])
        config["collective"].append(entry)

    return config


def config_to_engine(config: dict) -> ConstraintEngine:
    """Rebuild a ConstraintEngine from a saved config dict"""
    engine = ConstraintEngine(name=config.get("name", "ConstraintEngine"))

    for entry in config.get("separate", []):
        ctype = entry.get("type")
        if ctype in CONSTRAINT_REGISTRY:
            reg = CONSTRAINT_REGISTRY[ctype]
            kwargs = {"name": entry.get("name", ctype)}
            for param_name, param_info in reg["params"].items():
                kwargs[param_name] = entry.get(param_name, param_info["default"])
            engine.add_separate(reg["class"](**kwargs))

    for entry in config.get("collective", []):
        ctype = entry.get("type")
        if ctype in CONSTRAINT_REGISTRY:
            reg = CONSTRAINT_REGISTRY[ctype]
            kwargs = {"name": entry.get("name", ctype)}
            for param_name, param_info in reg["params"].items():
                kwargs[param_name] = entry.get(param_name, param_info["default"])
            engine.add_collective(reg["class"](**kwargs))

    return engine


# ============================================================================
# SHARED THEME - one light palette + one type scale used by EVERY window
# (main window, Constraint Engine Editor, Test popup, splash). Change a colour or a font
# here and it changes everywhere. lmsuite_token_tuner.py mirrors these same values.
# ============================================================================
PALETTE = {
    'bg': '#f4f5f7',             # page background behind the cards
    'card': '#ffffff',           # cards / panels
    'bar': '#ffffff',            # top menu bar
    'field': '#ffffff',          # text boxes, dropdowns
    'field_alt': '#f9fafb',      # read-only boxes (logs, outputs)
    'text': '#111827',           # main text
    'text_soft': '#374151',      # secondary text
    'muted': '#6b7280',          # hints, captions
    'on_accent': '#ffffff',      # text on any coloured button
    'accent': '#2563eb',         # primary blue
    'accent_hover': '#1d4ed8',
    'accent_soft': '#dbeafe',    # light blue highlight
    'success': '#15803d',        # green: start / apply / add / online
    'success_hover': '#166534',
    'danger': '#b91c1c',         # red: stop / remove / offline / errors
    'danger_hover': '#991b1b',
    'warning': '#b45309',        # amber: caution
    'warning_hover': '#92400e',
    'orange': '#ea580c',         # orange: soft refresh button
    'orange_hover': '#c2410c',
    'neutral': '#e5e7eb',        # grey buttons / inactive tabs
    'neutral_hover': '#d1d5db',
    'border': '#e5e7eb',         # card borders
    'border_strong': '#d1d5db',  # input borders
    'disabled': '#9ca3af',
}
FONT_UI = 'Segoe UI'       # all normal text
FONT_MONO = 'Consolas'     # logs, code-like values
# Type scale used everywhere: 15 title / 11 card header / 10 body / 9 small + mono

# Button looks by role, used as  tk.Button(..., **BUTTON_STYLES['primary'])
BUTTON_STYLES = {
    'primary': {'bg': PALETTE['accent'], 'fg': PALETTE['on_accent'],
                'activebackground': PALETTE['accent_hover'], 'activeforeground': PALETTE['on_accent']},
    'success': {'bg': PALETTE['success'], 'fg': PALETTE['on_accent'],
                'activebackground': PALETTE['success_hover'], 'activeforeground': PALETTE['on_accent']},
    'danger': {'bg': PALETTE['danger'], 'fg': PALETTE['on_accent'],
               'activebackground': PALETTE['danger_hover'], 'activeforeground': PALETTE['on_accent']},
    'warning': {'bg': PALETTE['warning'], 'fg': PALETTE['on_accent'],
                'activebackground': PALETTE['warning_hover'], 'activeforeground': PALETTE['on_accent']},
    'orange': {'bg': PALETTE['orange'], 'fg': PALETTE['on_accent'],
               'activebackground': PALETTE['orange_hover'], 'activeforeground': PALETTE['on_accent']},
    'neutral': {'bg': PALETTE['neutral'], 'fg': PALETTE['text'],
                'activebackground': PALETTE['neutral_hover'], 'activeforeground': PALETTE['text']},
}


# ============================================================================
# CONSTRAINT ENGINE — VISUAL EDITOR (ConstraintEngineGUI)
# ============================================================================

CE_COLORS = {
    'bg': PALETTE['bg'],
    'bg_card': PALETTE['card'],
    'bg_input': PALETTE['field'],
    'bg_alt': PALETTE['field_alt'],
    'bg_selected': PALETTE['accent_soft'],
    'text': PALETTE['text'],
    'text_muted': PALETTE['muted'],
    'text_heading': PALETTE['text'],
    'text_on_accent': PALETTE['on_accent'],
    'accent': PALETTE['accent'],
    'accent_hover': PALETTE['accent_hover'],
    'green': PALETTE['success'],
    'red': PALETTE['danger'],
    'orange': PALETTE['warning'],
    'yellow': PALETTE['warning'],
    'border': PALETTE['border'],
    'border_input': PALETTE['border_strong'],
    'separate_tag': PALETTE['success'],
    'collective_tag': PALETTE['accent'],
}

CE_FONTS = {
    'heading': (FONT_UI, 15, 'bold'),
    'subheading': (FONT_UI, 11, 'bold'),
    'normal': (FONT_UI, 10),
    'small': (FONT_UI, 9),
    'mono': (FONT_MONO, 10),          # list rows only (keeps the wrench-icon columns aligned)
    'mono_small': (FONT_MONO, 9),
}


class ConstraintEngineGUI:
    """Visual editor for ConstraintEngine configurations"""

    def __init__(self, root, engine: ConstraintEngine = None, on_apply=None):
        """
        root: tk.Tk or tk.Toplevel
        engine: existing ConstraintEngine to edit (or None for fresh)
        on_apply: callback(engine) called when user clicks Apply
        """
        self.root = root
        self.root.title("Constraint Engine Editor")
        self.root.geometry("820x700")
        self.root.configure(bg=CE_COLORS['bg'])
        self.root.resizable(True, True)
        self.root.minsize(700, 500)

        self.on_apply = on_apply
        self.config_path = os.path.join(os.path.expanduser("~"), "Downloads", "constraint_engine_config.json")
        self.unsaved_changes = False

        # Build working config from engine or defaults
        if engine:
            self.working_config = engine_to_config(engine)
        else:
            self.working_config = self._default_config()

        self._build_ui()
        self._refresh_list()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _default_config(self):
        return {
            "name": "StoryValidator",
            "separate": [
                {"type": "IsNonEmpty", "name": "IsNonEmpty"},
                {"type": "NoDoubleSpaces", "name": "NoDoubleSpaces"},
                {"type": "NoTrailingWhitespace", "name": "NoTrailingWhitespace"},
                {"type": "CapitalizeFirst", "name": "CapitalizeFirst"},
                {"type": "MaxLength", "name": "MaxLength", "max_len": 10000},
            ],
            "collective": [
                {"type": "NoContradictions", "name": "NoContradictions"},
            ]
        }

    # ====================================================================
    # UI BUILD
    # ====================================================================

    def _build_ui(self):
        # Title bar
        title_frame = tk.Frame(self.root, bg=CE_COLORS['bg'])
        title_frame.pack(fill=tk.X, padx=15, pady=(12, 0))

        tk.Label(title_frame, text="Constraint Engine Editor", font=CE_FONTS['heading'],
                 bg=CE_COLORS['bg'], fg=CE_COLORS['text_heading']).pack(side=tk.LEFT)

        self.status_label = tk.Label(title_frame, text="", font=CE_FONTS['small'],
                                      bg=CE_COLORS['bg'], fg=CE_COLORS['text_muted'])
        self.status_label.pack(side=tk.RIGHT)
        self._update_status()

        # Engine name row
        name_frame = tk.Frame(self.root, bg=CE_COLORS['bg'])
        name_frame.pack(fill=tk.X, padx=15, pady=(8, 0))

        tk.Label(name_frame, text="Engine Name:", font=CE_FONTS['normal'],
                 bg=CE_COLORS['bg'], fg=CE_COLORS['text_muted']).pack(side=tk.LEFT, padx=(0, 8))
        self.name_var = tk.StringVar(value=self.working_config.get("name", "ConstraintEngine"))
        name_entry = tk.Entry(name_frame, textvariable=self.name_var, font=CE_FONTS['normal'],
                              bg=CE_COLORS['bg_input'], fg=CE_COLORS['text'], insertbackground=CE_COLORS['text'],
                              relief=tk.FLAT, bd=0, width=30)
        name_entry.pack(side=tk.LEFT, padx=(0, 15), ipady=4, ipadx=6)
        self.name_var.trace_add("write", lambda *_: self._mark_changed())

        # Main split: left = constraint list, right = editor
        body = tk.Frame(self.root, bg=CE_COLORS['bg'])
        body.pack(fill=tk.BOTH, expand=True, padx=15, pady=(10, 0))
        body.columnconfigure(0, weight=1)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)

        # ── LEFT: Constraint List ──
        left = tk.Frame(body, bg=CE_COLORS['bg_card'], highlightthickness=1,
                        highlightbackground=CE_COLORS['border'])
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        left.rowconfigure(1, weight=1)
        left.columnconfigure(0, weight=1)

        # List header
        lh = tk.Frame(left, bg=CE_COLORS['bg_card'])
        lh.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 5))
        tk.Label(lh, text="Active Constraints", font=CE_FONTS['subheading'],
                 bg=CE_COLORS['bg_card'], fg=CE_COLORS['text_heading']).pack(side=tk.LEFT)

        # Constraint listbox
        list_frame = tk.Frame(left, bg=CE_COLORS['bg_input'], highlightthickness=1,
                              highlightbackground=CE_COLORS['border_input'])
        list_frame.grid(row=1, column=0, sticky="nsew", padx=10, pady=(0, 5))
        list_frame.rowconfigure(0, weight=1)
        list_frame.columnconfigure(0, weight=1)

        self.constraint_listbox = tk.Listbox(
            list_frame, font=CE_FONTS['mono'], bg=CE_COLORS['bg_input'], fg=CE_COLORS['text'],
            selectbackground=CE_COLORS['accent'], selectforeground=CE_COLORS['text_on_accent'],
            relief=tk.FLAT, bd=0, highlightthickness=0, activestyle='none'
        )
        self.constraint_listbox.grid(row=0, column=0, sticky="nsew")
        self.constraint_listbox.bind("<<ListboxSelect>>", self._on_select)

        scrollbar = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self.constraint_listbox.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.constraint_listbox.config(yscrollcommand=scrollbar.set)

        # List buttons
        lb = tk.Frame(left, bg=CE_COLORS['bg_card'])
        lb.grid(row=2, column=0, sticky="ew", padx=10, pady=(0, 10))

        tk.Button(lb, text="▲ Up", command=self._move_up,
                  **BUTTON_STYLES['neutral']).pack(side=tk.LEFT, padx=(0, 6))
        tk.Button(lb, text="▼ Down", command=self._move_down,
                  **BUTTON_STYLES['neutral']).pack(side=tk.LEFT, padx=(0, 6))
        tk.Button(lb, text="✕ Remove", command=self._remove_selected,
                  **BUTTON_STYLES['danger']).pack(side=tk.RIGHT)

        # ── RIGHT: Editor Panel ──
        right = tk.Frame(body, bg=CE_COLORS['bg_card'], highlightthickness=1,
                         highlightbackground=CE_COLORS['border'])
        right.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        right.rowconfigure(1, weight=1)
        right.columnconfigure(0, weight=1)

        # Editor header
        rh = tk.Frame(right, bg=CE_COLORS['bg_card'])
        rh.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 5))
        tk.Label(rh, text="Edit Constraint", font=CE_FONTS['subheading'],
                 bg=CE_COLORS['bg_card'], fg=CE_COLORS['text_heading']).pack(side=tk.LEFT)

        # Editor content (dynamic)
        self.editor_frame = tk.Frame(right, bg=CE_COLORS['bg_card'])
        self.editor_frame.grid(row=1, column=0, sticky="nsew", padx=10, pady=(0, 10))

        self._show_no_selection()

        # ── ADD CONSTRAINT SECTION ──
        add_frame = tk.Frame(self.root, bg=CE_COLORS['bg_card'], highlightthickness=1,
                             highlightbackground=CE_COLORS['border'])
        add_frame.pack(fill=tk.X, padx=15, pady=(10, 0))

        add_inner = tk.Frame(add_frame, bg=CE_COLORS['bg_card'])
        add_inner.pack(fill=tk.X, padx=10, pady=10)

        tk.Label(add_inner, text="Add Constraint:", font=CE_FONTS['subheading'],
                 bg=CE_COLORS['bg_card'], fg=CE_COLORS['text_heading']).pack(side=tk.LEFT, padx=(0, 10))

        # (packed before the dropdown/description so it always stays visible)
        tk.Button(add_inner, text="+ Add", command=self._add_constraint,
                  **BUTTON_STYLES['success']).pack(side=tk.RIGHT)

        # Type dropdown
        self.add_type_var = tk.StringVar(value="NoDoubleSpaces")
        type_names = sorted(CONSTRAINT_REGISTRY.keys())
        self.add_dropdown = ttk.Combobox(add_inner, textvariable=self.add_type_var,
                                          values=type_names, state="readonly", width=22)
        self.add_dropdown.pack(side=tk.LEFT, padx=(0, 8))
        self.add_dropdown.bind("<<ComboboxSelected>>", self._update_add_preview)

        # Preview label
        self.add_preview = tk.Label(add_inner, text="", font=CE_FONTS['small'],
                                     bg=CE_COLORS['bg_card'], fg=CE_COLORS['text_muted'])
        self.add_preview.pack(side=tk.LEFT, padx=(0, 10), fill=tk.X, expand=True)
        self._update_add_preview()

        # ── TEST SECTION ──
        test_frame = tk.Frame(self.root, bg=CE_COLORS['bg_card'], highlightthickness=1,
                              highlightbackground=CE_COLORS['border'])
        test_frame.pack(fill=tk.X, padx=15, pady=(10, 0))

        test_header = tk.Frame(test_frame, bg=CE_COLORS['bg_card'])
        test_header.pack(fill=tk.X, padx=10, pady=(10, 5))
        tk.Label(test_header, text="Test Engine", font=CE_FONTS['subheading'],
                 bg=CE_COLORS['bg_card'], fg=CE_COLORS['text_heading']).pack(side=tk.LEFT)

        tk.Button(test_header, text="▶ Run Test", command=self._run_test,
                  **BUTTON_STYLES['primary']).pack(side=tk.RIGHT)

        self.test_auto_fix_var = tk.BooleanVar(value=True)
        tk.Checkbutton(test_header, text="Auto-fix", variable=self.test_auto_fix_var,
                        font=CE_FONTS['normal'], bg=CE_COLORS['bg_card'], fg=CE_COLORS['text'],
                        selectcolor=CE_COLORS['bg_input'], activebackground=CE_COLORS['bg_card'],
                        activeforeground=CE_COLORS['text']).pack(side=tk.RIGHT, padx=(0, 10))

        test_body = tk.Frame(test_frame, bg=CE_COLORS['bg_card'])
        test_body.pack(fill=tk.X, padx=10, pady=(0, 10))
        test_body.columnconfigure(0, weight=1)
        test_body.columnconfigure(1, weight=1)

        self.test_input = tk.Text(test_body, height=3, font=CE_FONTS['mono_small'],
                                   bg=CE_COLORS['bg_input'], fg=CE_COLORS['text'],
                                   insertbackground=CE_COLORS['text'], relief=tk.FLAT, bd=0, wrap=tk.WORD)
        self.test_input.grid(row=0, column=0, sticky="nsew", padx=(0, 4), ipady=4, ipadx=4)
        self.test_input.insert("1.0", "  hello world.  This is a  test with  double spaces.  ")

        self.test_output = tk.Text(test_body, height=3, font=CE_FONTS['mono_small'],
                                    bg=CE_COLORS['bg_alt'], fg=CE_COLORS['green'],
                                    relief=tk.FLAT, bd=0, wrap=tk.WORD, state=tk.DISABLED)
        self.test_output.grid(row=0, column=1, sticky="nsew", padx=(4, 0), ipady=4, ipadx=4)

        # ── BOTTOM BUTTONS ──
        bottom = tk.Frame(self.root, bg=CE_COLORS['bg'])
        bottom.pack(fill=tk.X, padx=15, pady=(12, 15))

        tk.Button(bottom, text="💾 Save Config", command=self._save_config,
                  **BUTTON_STYLES['neutral']).pack(side=tk.LEFT, padx=(0, 6))

        tk.Button(bottom, text="📂 Load Config", command=self._load_config,
                  **BUTTON_STYLES['neutral']).pack(side=tk.LEFT, padx=(0, 6))

        tk.Button(bottom, text="🔄 Reset Defaults", command=self._reset_defaults,
                  **BUTTON_STYLES['warning']).pack(side=tk.LEFT)

        self.apply_btn = tk.Button(bottom, text="✓ Apply to Engine", command=self._apply,
                                    **BUTTON_STYLES['success'])
        self.apply_btn.pack(side=tk.RIGHT)

    # ====================================================================
    # CONSTRAINT LIST MANAGEMENT
    # ====================================================================

    def _get_all_entries(self):
        """Return flat list of (category, index, entry) for display order"""
        items = []
        for i, entry in enumerate(self.working_config.get("separate", [])):
            items.append(("separate", i, entry))
        for i, entry in enumerate(self.working_config.get("collective", [])):
            items.append(("collective", i, entry))
        return items

    def _refresh_list(self):
        self.constraint_listbox.delete(0, tk.END)

        items = self._get_all_entries()
        if not items:
            self.constraint_listbox.insert(tk.END, "  (no constraints loaded)")
            return

        last_cat = None
        for cat, idx, entry in items:
            if cat != last_cat:
                header = f"── {'SEPARATE (Fast)' if cat == 'separate' else 'COLLECTIVE (Global)'} ──"
                self.constraint_listbox.insert(tk.END, header)
                self.constraint_listbox.itemconfig(tk.END, fg=CE_COLORS['text_muted'], selectbackground=CE_COLORS['bg_input'],
                                                   selectforeground=CE_COLORS['text_muted'])
                last_cat = cat

            tag = "[S]" if cat == "separate" else "[C]"
            ctype = entry.get("type", "?")
            name = entry.get("name", ctype)

            # Show key param value if any
            reg = CONSTRAINT_REGISTRY.get(ctype, {})
            param_str = ""
            for pname, pinfo in reg.get("params", {}).items():
                val = entry.get(pname, pinfo["default"])
                if isinstance(val, list):
                    val = ", ".join(str(v) for v in val) if val else "(none)"
                param_str = f" = {val}"
                break  # Show first param only

            fix_icon = "🔧" if reg.get("auto_fix") else "  "
            line = f"  {tag} {fix_icon} {name}{param_str}"
            self.constraint_listbox.insert(tk.END, line)

        self._update_status()

    def _update_status(self):
        sep = len(self.working_config.get("separate", []))
        col = len(self.working_config.get("collective", []))
        changed = " ●" if self.unsaved_changes else ""
        self.status_label.config(text=f"{sep} separate, {col} collective{changed}")

    def _mark_changed(self):
        self.unsaved_changes = True
        self._update_status()

    def _resolve_selection(self):
        """Map listbox selection index to (category, config_index) or None"""
        sel = self.constraint_listbox.curselection()
        if not sel:
            return None

        lb_idx = sel[0]
        text = self.constraint_listbox.get(lb_idx)

        # Skip header rows
        if text.startswith("──") or text.startswith("  (no"):
            return None

        # Count real entries before this index
        real_idx = -1
        for i in range(lb_idx + 1):
            t = self.constraint_listbox.get(i)
            if not t.startswith("──") and not t.startswith("  (no"):
                real_idx += 1

        # Map to category + index
        sep_count = len(self.working_config.get("separate", []))
        if real_idx < sep_count:
            return ("separate", real_idx)
        else:
            return ("collective", real_idx - sep_count)

    def _on_select(self, event=None):
        resolved = self._resolve_selection()
        if resolved is None:
            self._show_no_selection()
            return

        cat, idx = resolved
        entry = self.working_config[cat][idx]
        self._show_editor(cat, idx, entry)

    def _move_up(self):
        resolved = self._resolve_selection()
        if resolved is None:
            return
        cat, idx = resolved
        if idx <= 0:
            return
        lst = self.working_config[cat]
        lst[idx], lst[idx - 1] = lst[idx - 1], lst[idx]
        self._mark_changed()
        self._refresh_list()
        # Reselect
        self._select_entry(cat, idx - 1)

    def _move_down(self):
        resolved = self._resolve_selection()
        if resolved is None:
            return
        cat, idx = resolved
        lst = self.working_config[cat]
        if idx >= len(lst) - 1:
            return
        lst[idx], lst[idx + 1] = lst[idx + 1], lst[idx]
        self._mark_changed()
        self._refresh_list()
        self._select_entry(cat, idx + 1)

    def _select_entry(self, cat, idx):
        """Select a specific entry in the listbox after refresh"""
        # Count listbox position: headers + entries
        lb_idx = 0
        # Separate header
        if self.working_config.get("separate"):
            lb_idx += 1  # header row
            if cat == "separate":
                lb_idx += idx
                self.constraint_listbox.selection_set(lb_idx)
                self.constraint_listbox.see(lb_idx)
                self._on_select()
                return
            lb_idx += len(self.working_config["separate"])

        # Collective header
        if self.working_config.get("collective"):
            lb_idx += 1  # header row
            if cat == "collective":
                lb_idx += idx
                self.constraint_listbox.selection_set(lb_idx)
                self.constraint_listbox.see(lb_idx)
                self._on_select()
                return

    def _remove_selected(self):
        resolved = self._resolve_selection()
        if resolved is None:
            return
        cat, idx = resolved
        entry = self.working_config[cat][idx]
        name = entry.get("name", entry.get("type", "?"))
        if messagebox.askyesno("Remove Constraint", f"Remove '{name}'?"):
            self.working_config[cat].pop(idx)
            self._mark_changed()
            self._refresh_list()
            self._show_no_selection()

    def _add_constraint(self):
        ctype = self.add_type_var.get()
        if ctype not in CONSTRAINT_REGISTRY:
            return

        reg = CONSTRAINT_REGISTRY[ctype]
        entry = {"type": ctype, "name": ctype}

        for pname, pinfo in reg["params"].items():
            entry[pname] = pinfo["default"]

        cat = reg["category"]
        self.working_config[cat].append(entry)
        self._mark_changed()
        self._refresh_list()

        # Select the new entry
        idx = len(self.working_config[cat]) - 1
        self._select_entry(cat, idx)

    def _update_add_preview(self, event=None):
        ctype = self.add_type_var.get()
        reg = CONSTRAINT_REGISTRY.get(ctype, {})
        cat = reg.get("category", "?")
        desc = reg.get("description", "")
        fix = "🔧 auto-fix" if reg.get("auto_fix") else "no auto-fix"
        tag = "Separate" if cat == "separate" else "Collective"
        self.add_preview.config(text=f"{tag} · {desc} · {fix}")

    # ====================================================================
    # EDITOR PANEL
    # ====================================================================

    def _show_no_selection(self):
        for w in self.editor_frame.winfo_children():
            w.destroy()

        tk.Label(self.editor_frame, text="Select a constraint\nfrom the list to edit it",
                 font=CE_FONTS['normal'], bg=CE_COLORS['bg_card'], fg=CE_COLORS['text_muted'],
                 justify=tk.CENTER).pack(expand=True)

    def _show_editor(self, cat, idx, entry):
        for w in self.editor_frame.winfo_children():
            w.destroy()

        ctype = entry.get("type", "?")
        reg = CONSTRAINT_REGISTRY.get(ctype, {})

        # Type and category
        header = tk.Frame(self.editor_frame, bg=CE_COLORS['bg_card'])
        header.pack(fill=tk.X, pady=(0, 10))

        tag_color = CE_COLORS['separate_tag'] if cat == "separate" else CE_COLORS['collective_tag']
        tag_text = "SEPARATE" if cat == "separate" else "COLLECTIVE"
        tk.Label(header, text=tag_text, font=CE_FONTS['small'], bg=tag_color,
                 fg=CE_COLORS['text_on_accent'], padx=6, pady=1).pack(side=tk.LEFT, padx=(0, 8))

        tk.Label(header, text=ctype, font=CE_FONTS['subheading'],
                 bg=CE_COLORS['bg_card'], fg=CE_COLORS['text_heading']).pack(side=tk.LEFT)

        # Description
        desc = reg.get("description", "No description")
        tk.Label(self.editor_frame, text=desc, font=CE_FONTS['small'],
                 bg=CE_COLORS['bg_card'], fg=CE_COLORS['text_muted'], anchor=tk.W).pack(fill=tk.X, pady=(0, 5))

        fix = "🔧 Can auto-fix failures" if reg.get("auto_fix") else "⚠ Cannot auto-fix (validate only)"
        tk.Label(self.editor_frame, text=fix, font=CE_FONTS['small'],
                 bg=CE_COLORS['bg_card'], fg=CE_COLORS['text_muted'], anchor=tk.W).pack(fill=tk.X, pady=(0, 12))

        # Separator
        tk.Frame(self.editor_frame, bg=CE_COLORS['border'], height=1).pack(fill=tk.X, pady=(0, 12))

        # Name field
        name_row = tk.Frame(self.editor_frame, bg=CE_COLORS['bg_card'])
        name_row.pack(fill=tk.X, pady=(0, 10))

        tk.Label(name_row, text="Display Name:", font=CE_FONTS['normal'],
                 bg=CE_COLORS['bg_card'], fg=CE_COLORS['text']).pack(anchor=tk.W)

        name_var = tk.StringVar(value=entry.get("name", ctype))
        name_entry = tk.Entry(name_row, textvariable=name_var, font=CE_FONTS['normal'],
                              bg=CE_COLORS['bg_input'], fg=CE_COLORS['text'],
                              insertbackground=CE_COLORS['text'], relief=tk.FLAT, bd=0)
        name_entry.pack(fill=tk.X, ipady=4, ipadx=6, pady=(3, 0))

        # Parameter fields
        param_vars = {}
        for pname, pinfo in reg.get("params", {}).items():
            row = tk.Frame(self.editor_frame, bg=CE_COLORS['bg_card'])
            row.pack(fill=tk.X, pady=(0, 10))

            tk.Label(row, text=f"{pinfo['label']}:", font=CE_FONTS['normal'],
                     bg=CE_COLORS['bg_card'], fg=CE_COLORS['text']).pack(anchor=tk.W)

            current_val = entry.get(pname, pinfo["default"])

            if pinfo["type"] == "int":
                var = tk.IntVar(value=current_val)
                spin = ttk.Spinbox(row, from_=pinfo.get("min", 0), to=pinfo.get("max", 999999),
                                    textvariable=var, width=12)
                spin.pack(anchor=tk.W, pady=(3, 0))
                param_vars[pname] = ("int", var)

            elif pinfo["type"] == "float":
                var = tk.DoubleVar(value=float(current_val))
                spin = ttk.Spinbox(row, from_=pinfo.get("min", 0.0), to=pinfo.get("max", 1.0),
                                    increment=0.05, textvariable=var, width=12)
                spin.pack(anchor=tk.W, pady=(3, 0))
                param_vars[pname] = ("float", var)

            elif pinfo["type"] == "str":
                var = tk.StringVar(value=current_val)
                ent = tk.Entry(row, textvariable=var, font=CE_FONTS['normal'],
                               bg=CE_COLORS['bg_input'], fg=CE_COLORS['text'],
                               insertbackground=CE_COLORS['text'], relief=tk.FLAT, bd=0)
                ent.pack(fill=tk.X, ipady=4, ipadx=6, pady=(3, 0))
                param_vars[pname] = ("str", var)

            elif pinfo["type"] == "list":
                # Show as comma-separated string
                if isinstance(current_val, list):
                    display = ", ".join(str(v) for v in current_val)
                else:
                    display = str(current_val)
                var = tk.StringVar(value=display)
                ent = tk.Entry(row, textvariable=var, font=CE_FONTS['normal'],
                               bg=CE_COLORS['bg_input'], fg=CE_COLORS['text'],
                               insertbackground=CE_COLORS['text'], relief=tk.FLAT, bd=0)
                ent.pack(fill=tk.X, ipady=4, ipadx=6, pady=(3, 0))

                tk.Label(row, text="Separate items with commas", font=CE_FONTS['small'],
                         bg=CE_COLORS['bg_card'], fg=CE_COLORS['text_muted']).pack(anchor=tk.W, pady=(2, 0))
                param_vars[pname] = ("list", var)

        if not reg.get("params"):
            tk.Label(self.editor_frame, text="This constraint has no configurable parameters.",
                     font=CE_FONTS['small'], bg=CE_COLORS['bg_card'], fg=CE_COLORS['text_muted']).pack(
                anchor=tk.W, pady=(0, 10))

        # Save button for this constraint
        tk.Frame(self.editor_frame, bg=CE_COLORS['bg_card']).pack(fill=tk.BOTH, expand=True)

        def _save_edits():
            entry["name"] = name_var.get().strip() or ctype
            for pname, (ptype, var) in param_vars.items():
                if ptype == "int":
                    try:
                        entry[pname] = var.get()
                    except (ValueError, tk.TclError):
                        entry[pname] = CONSTRAINT_REGISTRY[ctype]["params"][pname]["default"]
                elif ptype == "float":
                    try:
                        entry[pname] = var.get()
                    except (ValueError, tk.TclError):
                        entry[pname] = CONSTRAINT_REGISTRY[ctype]["params"][pname]["default"]
                elif ptype == "str":
                    entry[pname] = var.get()
                elif ptype == "list":
                    raw = var.get().strip()
                    if raw:
                        entry[pname] = [item.strip() for item in raw.split(",") if item.strip()]
                    else:
                        entry[pname] = []

            self._mark_changed()
            self._refresh_list()
            self._select_entry(cat, idx)

        btn_frame = tk.Frame(self.editor_frame, bg=CE_COLORS['bg_card'])
        btn_frame.pack(fill=tk.X, pady=(10, 0))

        tk.Button(btn_frame, text="💾 Save Changes", command=_save_edits,
                  **BUTTON_STYLES['primary']).pack(side=tk.RIGHT)

    # ====================================================================
    # TEST ENGINE
    # ====================================================================

    def _run_test(self):
        test_data = self.test_input.get("1.0", tk.END).strip()
        if not test_data:
            self._set_test_output("Enter test text first", CE_COLORS['red'])
            return

        # Build engine from current config
        try:
            engine = config_to_engine(self.working_config)
        except Exception as e:
            self._set_test_output(f"Engine build error: {e}", CE_COLORS['red'])
            return

        auto_fix = self.test_auto_fix_var.get()

        start = time.time()
        result = engine.validate(test_data, auto_fix=auto_fix)
        elapsed = (time.time() - start) * 1000

        lines = []
        for entry in result.get("log", []):
            name = entry["constraint"]
            phase = "S" if entry.get("phase") == "separate" else "C"
            ms = entry.get("duration_ms", 0)

            if entry.get("fixed"):
                lines.append(f"🔧 [{phase}] {name}: FIXED ({ms:.2f}ms)")
            elif entry["passed"]:
                lines.append(f"✓ [{phase}] {name}: passed ({ms:.2f}ms)")
            else:
                lines.append(f"✗ [{phase}] {name}: FAILED ({ms:.2f}ms)")

        fixes = sum(1 for e in result.get("log", []) if e.get("fixed"))

        if result.get("success"):
            lines.append(f"\n✓ ALL PASSED ({elapsed:.2f}ms, {fixes} fixes)")
            if auto_fix and "data" in result:
                output = result["data"]
                if output != test_data:
                    lines.append(f"\nFixed output:\n{output}")
            color = CE_COLORS['green']
        else:
            failed = result.get("failed_at", "unknown")
            lines.append(f"\n✗ FAILED at: {failed} ({elapsed:.2f}ms)")
            color = CE_COLORS['red']

        self._set_test_output("\n".join(lines), color)

    def _set_test_output(self, text, color=CE_COLORS['green']):
        self.test_output.config(state=tk.NORMAL, fg=color)
        self.test_output.delete("1.0", tk.END)
        self.test_output.insert("1.0", text)
        self.test_output.config(state=tk.DISABLED)

    # ====================================================================
    # SAVE / LOAD / APPLY
    # ====================================================================

    def _save_config(self):
        self.working_config["name"] = self.name_var.get().strip() or "ConstraintEngine"

        path = filedialog.asksaveasfilename(
            title="Save Constraint Config",
            initialdir=os.path.expanduser("~/Downloads"),
            initialfile="constraint_engine_config.json",
            defaultextension=".json",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")]
        )
        if not path:
            return

        try:
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(self.working_config, f, indent=2)
            self.config_path = path
            self.unsaved_changes = False
            self._update_status()
            messagebox.showinfo("Saved", f"Config saved:\n{os.path.basename(path)}")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to save:\n{e}")

    def _load_config(self):
        path = filedialog.askopenfilename(
            title="Load Constraint Config",
            initialdir=os.path.expanduser("~/Downloads"),
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")]
        )
        if not path:
            return

        try:
            with open(path, 'r', encoding='utf-8') as f:
                loaded = json.load(f)

            # Validate structure
            if "separate" not in loaded and "collective" not in loaded:
                messagebox.showerror("Error", "Invalid config file — missing separate/collective keys")
                return

            self.working_config = loaded
            self.name_var.set(loaded.get("name", "ConstraintEngine"))
            self.config_path = path
            self.unsaved_changes = False
            self._refresh_list()
            self._show_no_selection()
            messagebox.showinfo("Loaded", f"Config loaded:\n{os.path.basename(path)}")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to load:\n{e}")

    def _reset_defaults(self):
        if messagebox.askyesno("Reset", "Reset all constraints to defaults?"):
            self.working_config = self._default_config()
            self.name_var.set(self.working_config["name"])
            self._mark_changed()
            self._refresh_list()
            self._show_no_selection()

    def _apply(self):
        """Build engine from config and fire the callback"""
        self.working_config["name"] = self.name_var.get().strip() or "ConstraintEngine"

        try:
            engine = config_to_engine(self.working_config)
        except Exception as e:
            messagebox.showerror("Error", f"Failed to build engine:\n{e}")
            return

        sep = len(engine.separate_constraints)
        col = len(engine.collective_constraints)

        if self.on_apply:
            self.on_apply(engine)
            self.unsaved_changes = False
            self._update_status()
            messagebox.showinfo("Applied", f"Engine applied: {sep} separate, {col} collective constraints")
        else:
            # Standalone mode — just confirm it builds
            self.unsaved_changes = False
            self._update_status()
            messagebox.showinfo("Validated",
                                f"Engine builds successfully:\n{sep} separate, {col} collective constraints\n\n"
                                f"(No LMSuite connected — save config to use it)")

    def _on_close(self):
        if self.unsaved_changes:
            if not messagebox.askyesno("Unsaved Changes", "You have unsaved changes. Close anyway?"):
                return
        self.root.destroy()

    def get_engine(self) -> ConstraintEngine:
        """Build and return engine from current config"""
        self.working_config["name"] = self.name_var.get().strip() or "ConstraintEngine"
        return config_to_engine(self.working_config)

# ============================================================================
# CONFIGURATION - Edit everything here to customize the app
# ============================================================================
CONFIG = {
    # ===== API SETTINGS =====
    'API': {
        'base_url': 'http://localhost:1234',
        'models_endpoint': '/api/v1/models',
        'load_model_endpoint': '/api/v1/models/load',
        'unload_model_endpoint': '/api/v1/models/unload',   # needs LM Studio 0.4.0+
        'chat_endpoint': '/v1/chat/completions',
        'timeout_connect': 2,
        'timeout_models': 5,
        'timeout_load': 30,
        'timeout_swap_load': 300,   # auto load/unload waits for big models to finish loading
        'timeout_chat': 300,
    },

    # ===== COLORS =====  (values come from PALETTE near the top of this file)
    'COLORS': {
        'bg_primary': PALETTE['bg'],           # Main background
        'bg_secondary': PALETTE['field_alt'],  # Light gray backgrounds (read-only boxes)
        'bg_white': PALETTE['card'],           # Card backgrounds
        'bg_menu': PALETTE['bar'],             # Menu bar background
        'bg_input': PALETTE['field'],          # Input field background

        'text_primary': PALETTE['text'],       # Main text
        'text_secondary': PALETTE['text_soft'],  # Headings
        'text_muted': PALETTE['muted'],        # Faded text
        'text_white': PALETTE['on_accent'],    # White text
        'text_error': PALETTE['danger'],       # Error red
        'text_blue': PALETTE['accent'],        # Link blue
        'text_warning': PALETTE['warning'],    # Amber warning text

        'button_primary': PALETTE['accent'],   # Main button color
        'button_green': PALETTE['success'],    # Save/success buttons
        'button_red': PALETTE['danger'],       # Delete/cancel buttons
        'button_disabled': PALETTE['disabled'],  # Disabled button

        'border_light': PALETTE['border'],     # Card borders
        'border_gray': PALETTE['border_strong'],  # Separators / input borders
        'border_dark': PALETTE['border'],      # Menu borders

        'status_online': PALETTE['success'],   # Connected status
        'status_offline': PALETTE['danger'],   # Disconnected status
        'tab_active': PALETTE['accent'],       # Active tab background
        'tab_inactive': PALETTE['neutral'],    # Inactive tab background
    },

    # ===== FONTS =====  (one type scale for every window: 15 / 11 / 10 / 9)
    'FONTS': {
        'family_default': FONT_UI,
        'family_mono': FONT_MONO,

        'size_large': 15,                  # Large titles
        'size_title': 11,                  # Window/card titles
        'size_header': 11,                 # Section headers
        'size_normal': 10,                 # Normal text
        'size_small': 9,                   # Small text
        'size_tiny': 9,                    # Tiny text
    },

    # ===== DIMENSIONS =====
    'DIMENSIONS': {
        'window_width': 900,
        'window_height': 624,
        'splash_width': 400,
        'splash_height': 150,
        'settings_width': 550,
        'settings_height': 500,

        'padding_huge': 20,                # Large padding
        'padding_large': 15,               # Card padding
        'padding_normal': 12,              # Standard padding
        'padding_small': 8,                # Small padding
        'padding_tiny': 3,                 # Tiny padding

        'margin_large': 15,                # Large margins
        'margin_normal': 12,               # Standard margins
        'margin_small': 5,                 # Small margins

        'chat_height': 200,                # Chat message container height
        'final_output_height': 16,         # Final Output text area height
        'model_list_height': 4,            # Model listbox height
        'chat_input_height': 2,            # Chat input height

        'card_relief': tk.FLAT,
        'card_border_width': 1,
        'card_border': tk.SOLID,
    },

    # ===== TEXT & LABELS =====
    'TEXT': {
        # Window titles
        'app_title': 'LMSuite — LM Studio Multi-Model Orchestrator',
        'settings_title': 'Settings (beta)',
        'error_title': 'LM Studio Error',

        # Status messages
        'status_connected': '✅ Connected',
        'status_disconnected': '❌ Disconnected',
        'status_model_loaded': '✅ Model Loaded',
        'status_no_model': '❌ No Model Loaded',
        'status_waiting': 'Waiting...',
        'status_offline': 'Offline',
        'status_thinking': 'Thinking...',
        'status_detecting': 'Detecting...',

        # Card headers
        'card_status': 'Status',
        'card_model_loader': 'Model Loader',
        'card_chat': 'Chat',

        # Labels & fields
        'label_lm_studio_status': 'LM Studio Status:',
        'label_server': 'Server:',
        'label_model': 'Model:',
        'label_model_loaded': 'Model:',
        'label_tokens_used': 'Tokens used:',
        'label_reasoning': 'Reasoning:',
        'label_max_tokens': 'Max Tokens:',
        'label_temperature': 'Temperature:',
        'label_available_models': 'Available Models:',
        'label_double_click': '(double-click to load)',
        'label_system_resources': 'System Resources:',
        'label_vram': 'VRAM:',
        'label_ram': 'RAM:',
        'label_total': 'Total:',
        'label_pass': 'Pass',
        'label_create': 'Create',
        'label_refine': 'Refine',
        'label_polish': 'Polish',

        # Button labels
        'btn_refresh': 'Soft Refresh',
        'btn_restart': 'Hard Restart',
        'btn_send': '➤',
        'btn_clear_chat': '🗑️ Clear',
        'btn_file': 'File',
        'btn_settings': 'Settings (beta)',
        'btn_view_log': 'View Log File',
        'btn_exit': 'Exit',
        'btn_apply_test': '✓ Apply & Test',
        'btn_close': 'Close',
        'btn_apply': '✓ Apply',
        'btn_save': 'Save',
        'btn_cancel': 'Cancel',
        'btn_pick_color': 'Pick Color',

        # Section headers in settings
        'settings_server_config': '🌐 Server Configuration',
        'settings_api_info': '📡 APIs Used',
        'settings_dev_settings': '🛠️  Developer Settings',
        'settings_color_custom': '🎨 Color Customization',
        'settings_header_custom': '📝 Section Header Customization',

        # Settings fields
        'settings_server_addr': 'LM Studio Server Address:',
        'settings_default': 'Default: http://localhost:1234',
        'settings_quick_start': 'Skip loading splash screen (Quick Start)',
        'settings_quick_start_desc': 'Bypasses the startup connection check',
        'settings_main_res': 'Main Window Resolution:',
        'settings_splash_res': 'Splash Window Resolution:',
        'settings_splash_note': 'Note: Restart app for resolution changes to take effect',
        'settings_text_color': 'Main Text Color:',
        'settings_heading_color': 'Section Header Color:',
        'settings_bg_color': 'Background Color:',
        'settings_button_color': 'Button Color:',
        'settings_font_size': 'Font Size:',
        'settings_font_weight': 'Font Weight:',

        # API info text
        'api_models_list': '• /api/v1/models - List available models',
        'api_models_load': '• /api/v1/models/load - Load a specific model',
        'api_chat': '• /v1/chat/completions - Chat endpoint for responses',

        # Messages
        'msg_no_model_selected': 'Please select a model for chat.',
        'msg_empty_message': 'Please type a message.',
        'msg_no_log': 'Log file not found:',
        'msg_restart_confirm': 'Restart? Token count will reset.',
        'msg_clear_logs': 'Clear all logs?',
        'msg_log_copied': 'Log entry copied!',
        'msg_no_selection': 'Select a log entry first.',
        'msg_no_model_identified': 'Could not identify model',
        'msg_empty_server': 'Please enter a server address first.',

        # Error messages
        'error_failed_load': 'Failed to load model.\nStatus:',
        'error_load_model': 'Error loading model:',
        'error_api_error': 'API Error:',
        'error_connection': 'Failed to connect to LM Studio:',
        'error_unexpected': 'Unexpected response format from LM Studio.',
        'error_save_file': 'Could not save:',
        'error_invalid_input': 'Invalid input',
        'error_invalid_resolution': 'Resolution values must be numbers!',

        # Popup titles
        'popup_success': 'Success',
        'popup_connected': 'Connected to server!',
        'popup_error': 'Error',
        'popup_warning': 'Warning',
        'popup_info': 'Info',

        # Discussion rules (moved over from the old Research card)
        'label_discussion_rules': 'Discussion Rules (each one adds a real extra check to every turn):',
        'msg_new_topic_title': 'New Topic',

        # Discussion mode
        'card_discussion': 'Discussion',
        'label_discussion_topic': 'Discussion Topic / Question:',
        'label_discussion_participants': 'Participants:',
        'label_discussion_role': 'Role:',
        'label_discussion_auto_merge': 'Auto-Merge:',
        'label_discussion_log': 'Discussion Log:',
        'btn_add_participant': '+ Add Participant',
        'btn_remove_participant': '✕ Remove',
        'btn_discussion_start': '▶ Start',
        'btn_discussion_pause': '⏸ Pause',
        'btn_discussion_resume': '▶ Resume',
        'btn_discussion_stop': '■ Stop',
        'btn_open_transcript': '📄 Open Transcript',
        'btn_compact_discussion_now': '🔄 Compact Now',
        'status_discussion_stopped': '■ Stopped',
        'status_discussion_running': '● Running',
        'status_discussion_paused': '⏸ Paused',
        'msg_discussion_running': 'Discussion is already running.',
        'msg_no_discussion_topic': 'Enter a discussion topic/question first.',
        'msg_discussion_too_few': 'Add at least 2 participants (with models and roles selected).',
        'msg_no_transcript_yet': 'No transcript saved yet.',

        # Final Writer (Discussion card)
        'card_final_writer': 'Final Writer',
        'hint_final_writer': ('Turns the finished discussion into the piece you chose. It runs by itself when a '
                              'discussion finishes if Auto-write is ticked, or press Write Final Piece to run it '
                              'on the current discussion at any time.'),
        'label_final_writer': 'Final Writer Model:',
        'label_output_category': 'Turn the discussion into:',
        'label_writer_instructions': 'Extra instructions (optional):',
        'label_writer_temperature': 'Temperature:',
        'label_writer_max_tokens': 'Max tokens:',
        'label_server_timeout': 'Server Timeout (all model calls):',
        'chk_writer_auto': 'Auto-write the final piece when the discussion finishes',
        'btn_write_final': '✍ Write Final Piece',
        'label_final_output': 'Final Output:',
        'btn_copy_final': '📋 Copy',
        'btn_save_final': '💾 Save',
        'msg_writer_pause_first': 'Pause or stop the discussion before writing the final piece.',
        'msg_writer_in_progress': 'The Final Writer is already working.',
        'msg_no_final_writer': 'Select a Final Writer model first.',
        'msg_no_final_output': 'No final piece to copy or save yet.',
        'msg_final_copied': 'Final piece copied to clipboard!',
        'msg_pause_before_compact_discussion': 'Pause or stop the discussion before compacting manually — '
                                                'they both write to the same transcript.',
    },

    # ===== DEFAULT VALUES =====
    'DEFAULTS': {
        'max_tokens': 2000,
        'temperature': 0.7,
        'writer_temperature': 0.8,
        'writer_max_tokens': 8000,
        'model_dropdown_width': 25,
        'model_display_width': 22,
    },

    # ===== OUTPUT CATEGORIES — what kind of piece the Final Writer produces =====
    'OUTPUT_CATEGORIES': [
        'Story',
        'Coding',
        'Music',
        'Sports',
        'Movies',
        'Jokes',
        'Advice',
        'Electronics',
        'Articles',
        'Scripts',
        'Marketing',
        'Educational',
    ],

    # ===== OUTPUT PROFILES — what the Final Writer writes for each Category =====
    # The "Turn the discussion into" dropdown on the Discussion card picks one of these.
    #   form          — what is being produced, e.g. "working program"
    #   create_focus  — extra instruction given to the Final Writer for that kind of piece
    # Keep these strings free of curly braces — they end up inside str.format() templates.
    # To add a category: add its name to OUTPUT_CATEGORIES and an entry here with both keys.
    'OUTPUT_PROFILES': {
        'Story': {
            'form': 'short story',
            'create_focus': 'Make it vivid, with interesting characters and a clear narrative arc, around 400-600 words.',
        },
        'Coding': {
            'form': 'working program',
            'create_focus': 'Write complete, runnable code with no placeholders or TODO stubs, a sensible structure and brief comments. Return the code itself.',
        },
        'Music': {
            'form': 'set of song lyrics',
            'create_focus': 'Give it a clear song structure (verses, a chorus, and a bridge if it suits) with a consistent rhythm and a memorable hook.',
        },
        'Sports': {
            'form': 'sports piece',
            'create_focus': 'Write it like a knowledgeable sports writer: concrete details, a clear angle and an engaging voice.',
        },
        'Movies': {
            'form': 'film treatment',
            'create_focus': 'Cover the premise, the main characters, the three-act structure and the key scenes.',
        },
        'Jokes': {
            'form': 'set of jokes',
            'create_focus': 'Aim for tight setups and strong punchlines, with variety in style.',
        },
        'Advice': {
            'form': 'piece of practical advice',
            'create_focus': 'Be specific, actionable and empathetic, and explain the reasoning behind each recommendation.',
        },
        'Electronics': {
            'form': 'electronics project guide',
            'create_focus': 'Cover the components, how they connect, how it works and any safety notes, with concrete values where relevant.',
        },
        'Articles': {
            'form': 'article',
            'create_focus': 'Give it a strong hook, a clear structure and a well-supported argument or narrative.',
        },
        'Scripts': {
            'form': 'script',
            'create_focus': 'Use proper script format with scene headings, action lines and dialogue. Keep the dialogue natural and the characters distinct.',
        },
        'Marketing': {
            'form': 'piece of marketing copy',
            'create_focus': 'Lead with the benefit, speak directly to the target audience and end with a clear call to action.',
        },
        'Educational': {
            'form': 'lesson',
            'create_focus': 'Explain the concepts step by step with clear examples, and finish with a few check-your-understanding questions.',
        },
    },

    # ===== CONSTRAINT ENGINE EXCEPTIONS =====
    # The default constraints (NoDoubleSpaces, CapitalizeFirst, MaxLength...) are tuned for prose.
    # On code they would collapse indentation, capitalise the first keyword and truncate long
    # files, so the Final Writer skips the engine for these Categories.
    'ENGINE_SKIP_CATEGORIES': ['Coding'],

    # ===== FINAL WRITER =====
    # When a discussion finishes (or you press "Write Final Piece"), the Final Writer model gets
    # the topic plus everything the discussion produced and writes the finished piece in the
    # Category chosen on the Discussion card. Keep the template's {placeholders} — they are
    # filled in by _run_final_writer().
    'FINAL_WRITER': {
        # Up to this many characters of the full raw transcript go to the Final Writer as-is.
        # Longer discussions fall back to: Moderator synthesis + compacted summary + latest turns.
        'knowledge_max_chars': 16000,
        'final_writer_template': (
            "You are the Final Writer. A panel of AI participants has just finished discussing the "
            "topic below. Write {article} {form} that draws on EVERYTHING established in the "
            "discussion — the points they agreed on, the specific details, ideas and facts that "
            "came up, and how disagreements were resolved.\n\n"
            "ORIGINAL TOPIC: {topic}\n\n"
            "{instructions_block}"
            "DISCUSSION KNOWLEDGE:\n{knowledge}\n\n"
            "{create_focus}\n\n"
            "Return ONLY the finished {form}, with no preamble, commentary or explanation."
        ),
    },

    # ===== DISCUSSION RULES — opt-in checkboxes on the Discussion card (these are the old
    # Research Rules, retargeted at discussion turns). Each key here becomes one checkbox; each is
    # read fresh on every turn, so toggling one mid-run takes effect on the next turn. A turn is
    # split into numbered statements and each rule checks those. Rules that check statements only
    # ever judge FACTUAL ones — opinions, arguments and questions are always let through, so a
    # Continuity & Critic isn't penalised for arguing.
    # To add a rule: add an entry here (label + templates), handle it in _apply_discussion_rules,
    # and the checkbox appears automatically.
    'DISCUSSION_RULES': {
        'double_check': {
            'label': 'Double-Check Claims',
            'desc': 'The speaker re-reads its own turn and retracts any factual statement it is not confident about; retracted statements are removed.',
            'default': False,
            'verify_template': (
                "Topic: {topic}\n\n"
                "You just said the following in a discussion, split into numbered statements:\n{claims_list}\n\n"
                "Re-examine each statement carefully and independently. Only FACTUAL statements can be "
                "retracted — opinions, arguments, questions and transitions are always CONFIRM. For each "
                "statement, respond on its own line in EXACTLY this format (one line per statement, in "
                "order):\n<number>. CONFIRM or RETRACT\n\n"
                "Retract a factual statement ONLY if, on reflection, you are genuinely not confident it is accurate."
            ),
        },
        'verify_duplicates': {
            'label': 'Verify Duplicates (strict)',
            'desc': 'Uses a stricter similarity threshold when checking whether a turn just repeats an earlier one (so stalls are caught sooner).',
            'default': False,
            'strict_threshold': 0.55,   # lower = catches more paraphrased near-duplicates (default is DISCUSSION dedup_similarity_threshold)
        },
        'investigate': {
            'label': 'Investigate Deeper',
            'desc': 'After each new turn, the speaker follows up on its strongest factual claim with more specific detail, added to the turn.',
            'default': False,
            'followup_template': (
                "Topic: {topic}\n\n"
                "You just said this in a discussion:\n\"{turn}\"\n\n"
                "Pick the single most important factual claim in what you said and investigate it further. "
                "State 1-3 additional, MORE SPECIFIC facts that add depth or context to that claim "
                "(mechanisms, numbers, causes, exceptions, related specifics) — not restatements. Write "
                "them as one short paragraph with no preamble.\n\n"
                "If you have nothing more specific to add, respond with exactly: NO FURTHER DETAIL"
            ),
        },
        'verify_claims': {
            'label': 'Verify Claims (self-knowledge check)',
            'desc': 'Independently checks each factual statement against the model’s own trained knowledge (discussion hidden) and removes any it finds questionable.',
            'default': False,
            'selfcheck_template': (
                "Topic: {topic}\n\n"
                "Using ONLY your own trained knowledge, assess whether each of the following statements is "
                "consistent with what you actually know. Opinions, arguments, questions and transitions are "
                "always CONSISTENT; only factual statements can be QUESTIONABLE.\n\n{claims_list}\n\n"
                "Respond one line per statement, in order, in EXACTLY this format:\n"
                "<number>. CONSISTENT or QUESTIONABLE"
            ),
        },
        'cross_check': {
            'label': 'Cross-Check vs Discussion',
            'desc': 'Compares each turn with everything said so far and, if it conflicts with an earlier point, adds a visible note so the next speakers reconcile it.',
            'default': False,
            'crosscheck_template': (
                "Topic: {topic}\n\n"
                "DISCUSSION SO FAR:\n{existing_summary}\n\n"
                "NEW STATEMENTS (from the latest turn):\n{claims_list}\n\n"
                "Does any new statement directly CONTRADICT an earlier factual claim in the discussion, or "
                "the speaker's own earlier position (an actual conflict)? Do NOT flag new information, "
                "restatements, or disagreement that is openly framed as an objection to someone else's "
                "point. Respond one line per new statement, in order, in EXACTLY this format:\n"
                "<number>. OK or CONTRADICTS"
            ),
        },
    },

    # ===== DISCUSSION MODE — multi-model roundtable with shared, compacted transcript =====
    # Loads each speaker's model as needed (Auto load/unload), compacts and de-duplicates as it goes, and N participants take
    # turns responding to a running transcript. See DISCUSSION_ROLES below for what each
    # named role actually sends, DISCUSSION_RULES for the optional per-turn checks and
    # FINAL_WRITER for what happens when a discussion finishes.
    'DISCUSSION': {
        'summary_context_chars': 6000,        # how much compacted history to show each speaker
        'recent_turns_shown': 6,               # how many most-recent raw turns are shown in full
        'dedup_similarity_threshold': 0.75,    # difflib cutoff for "this turn just repeats an earlier one"
        'stall_threshold': 3,                  # this many near-duplicate turns in a row -> auto-pause
        'compact_every_n_rounds': 10,          # fallback/default — the UI's Auto-Merge dropdown overrides
        'moderator_every_n_rounds': 0,         # 0 = moderator only runs at the end / on demand
        'turn_max_tokens': 5000,
        'moderator_max_tokens': 3000,
        'rule_max_statements': 15,             # Discussion Rules check at most this many statements per turn
        'compaction_template': (
            "Topic under discussion: {topic}\n\n"
            "Below is the discussion transcript so far, which may include an already-condensed "
            "summary of earlier turns followed by more recent raw turns.\n\n{full_transcript}\n\n"
            "Rewrite this into a compact running summary: preserve every distinct point, claim, "
            "objection, and open question that has been raised, attributed to who raised it (by "
            "role), but condense repeated or superseded points and drop pure pleasantries. Organize "
            "it as short bullet-free prose grouped under short subtopic headings ('## <Subtopic>'). "
            "This summary will be shown to participants in place of the raw early turns, so it must "
            "stand alone."
        ),
    },

    # ===== DISCUSSION ROLES — per-participant persona =====
    # Each participant row picks one of these. 'turn_template' takes {topic}, {transcript} (the
    # compacted history + recent raw turns) and returns what that participant says this round.
    # Add more entries here (with a 'turn_template') to add more roles to the dropdown — nothing
    # else in the code needs to change. (The end-of-discussion Moderator synthesis only runs if a
    # role named 'Moderator' with a 'synthesis_template' is added back here.)
    'DISCUSSION_ROLES': {
        'Researcher': {
            # One real-world fact per turn (science, history, technology, geography, terminology).
            'turn_template': (
                "Topic under discussion: {topic}\n\n"
                "DISCUSSION SO FAR:\n{transcript}\n\n"
                "As the Researcher, contribute exactly ONE concrete, accurate real-world fact (science, history, technology, geography or correct terminology) that the others can build on or ground their ideas in. "
                "Pick a fact that has not already been stated in the discussion so far, and do not just restate what others already said. "
                "State that single fact in one or two sentences, with no list, no second fact, and no preamble."
            ),
        },
        'Worldbuilder': {
            # Creates the setting, environments, civilizations, ecosystems, technology, and rules.
            'turn_template': (
                "Topic under discussion: {topic}\n\n"
                "DISCUSSION SO FAR:\n{transcript}\n\n"
                "As the Worldbuilder, develop the setting: its environments, civilizations, ecosystems, technology and the underlying rules of how this world works. Add specific, concrete detail that fits what has been established. "
                "Do not just restate what others already said. Keep it to a short paragraph."
            ),
        },
        'Concept Creator': {
            # Generates unusual ideas, possibilities, twists, creatures, locations, and phenomena.
            'turn_template': (
                "Topic under discussion: {topic}\n\n"
                "DISCUSSION SO FAR:\n{transcript}\n\n"
                "As the Concept Creator, generate 1-3 unusual, original ideas: possibilities, twists, creatures, locations or phenomena that nobody has suggested yet. Favour the surprising over the obvious. "
                "Do not just restate what others already said. Keep it to a short paragraph."
            ),
        },
        'Speculative Scientist': {
            # Makes fictional concepts scientifically plausible where possible, while allowing speculation.
            'turn_template': (
                "Topic under discussion: {topic}\n\n"
                "DISCUSSION SO FAR:\n{transcript}\n\n"
                "As the Speculative Scientist, take the fictional concepts raised so far and make them scientifically plausible where possible, explaining how they could work. Where real science runs out, speculate openly and say so, without rejecting the premise. "
                "Do not just restate what others already said. Keep it to a short paragraph."
            ),
        },
        'Character Designer': {
            # Develops characters, personalities, motivations, relationships, flaws, and development.
            'turn_template': (
                "Topic under discussion: {topic}\n\n"
                "DISCUSSION SO FAR:\n{transcript}\n\n"
                "As the Character Designer, develop the characters: their personalities, motivations, relationships, flaws and how they could change over the story. Create new characters only if the story needs them. "
                "Do not just restate what others already said. Keep it to a short paragraph."
            ),
        },
        'Plot Architect': {
            # Builds the overall story structure, events, progression, and major turning points.
            'turn_template': (
                "Topic under discussion: {topic}\n\n"
                "DISCUSSION SO FAR:\n{transcript}\n\n"
                "As the Plot Architect, build the story's structure: the key events, how the story progresses and its major turning points. Say how the ideas so far fit into a coherent sequence. "
                "Do not just restate what others already said. Keep it to a short paragraph."
            ),
        },
        'Mystery & Conflict Designer': {
            # Creates mysteries, discoveries, obstacles, threats, secrets, and stakes.
            'turn_template': (
                "Topic under discussion: {topic}\n\n"
                "DISCUSSION SO FAR:\n{transcript}\n\n"
                "As the Mystery & Conflict Designer, create the mysteries, discoveries, obstacles, threats and secrets that drive the story, and make clear what is at stake and for whom. "
                "Do not just restate what others already said. Keep it to a short paragraph."
            ),
        },
        'Theme & Emotion Designer': {
            # Develops themes, emotional arcs, atmosphere, meaning, and character impact.
            'turn_template': (
                "Topic under discussion: {topic}\n\n"
                "DISCUSSION SO FAR:\n{transcript}\n\n"
                "As the Theme & Emotion Designer, develop the story's themes, emotional arcs, atmosphere and meaning, and how events should affect the characters and the reader. "
                "Do not just restate what others already said. Keep it to a short paragraph."
            ),
        },
        'Continuity & Critic': {
            # Checks contradictions, pacing problems, weak ideas, cliches, and inconsistencies without rejecting the fictional premise.
            'turn_template': (
                "Topic under discussion: {topic}\n\n"
                "DISCUSSION SO FAR:\n{transcript}\n\n"
                "As the Continuity & Critic, check the discussion so far for contradictions, inconsistencies, pacing problems, weak ideas and cliches, and say specifically what to fix and why. Accept the fictional premise as given and do not object to it. If nothing needs fixing, say so briefly. "
                "Do not just restate what others already said. Keep it to a short paragraph."
            ),
        },
        'Fact Checker': {
            # Audits claims presented as real-world fact; corrects errors; leaves invented fiction alone.
            'turn_template': (
                "Topic under discussion: {topic}\n\n"
                "DISCUSSION SO FAR:\n{transcript}\n\n"
                "As the Fact Checker, pick out the claims made so far that are presented as real-world fact (science, history, geography, technology, terminology, numbers) and check each one: say which are correct, which are wrong or misleading (giving the correction), and which you cannot confirm. Accept invented fiction and clearly speculative ideas as given and only check what is presented as real. If you are not sure whether something is true, say you are not sure rather than guessing. If there are no real-world claims to check, say so briefly. "
                "Do not just restate what others already said. Keep it to a short paragraph."
            ),
        },
        'Verification': {
            # Checks that the conclusions reached actually follow from what was established.
            'turn_template': (
                "Topic under discussion: {topic}\n\n"
                "DISCUSSION SO FAR:\n{transcript}\n\n"
                "As the Verifier, go through the key claims, conclusions and decisions reached so far and verify them: does each one follow from what was actually established in the discussion, is the reasoning sound, and do the numbers and details agree with each other? Sort them into VERIFIED, UNSUPPORTED (stated but never backed up) and CONTRADICTED, and say what would settle each unsupported one. Accept the fictional premise as given. If everything holds up, say so briefly. "
                "Do not just restate what others already said. Keep it to a short paragraph."
            ),
        },
        'Hallucination Checker': {
            # Hunts for details that look invented but are presented as real: names, dates, stats, quotes, sources.
            'turn_template': (
                "Topic under discussion: {topic}\n\n"
                "DISCUSSION SO FAR:\n{transcript}\n\n"
                "As the Hallucination Checker, look for details that may have been invented and presented as real: names, dates, statistics, quotes, sources, studies, places or technical terms that sound plausible but that you cannot confirm exist. For each suspect detail, say why it looks made up (too precise, no known source, does not match what you know) and how it should be fixed or removed. Do not flag deliberate fiction or clearly labelled speculation. If you cannot tell whether something is real, say so rather than asserting either way. If nothing looks fabricated, say so briefly. "
                "Do not just restate what others already said. Keep it to a short paragraph."
            ),
        },
    },
}

# ============================================================================
# END CONFIGURATION
# ============================================================================

# ============================================================================
# PROMPT EDITOR (built in) - this used to be the separate lm_prompt_editor.py.
# Opened from the Constraints Engine card (📝 Prompt Editor). Edits every prompt this file
# sends to a model (Discussion Roles, Discussion Rules, the compaction prompt, the Final
# Writer template and the Output Profile form/focus text) and saves the result as a "master
# prompt" file (~/Downloads/lm_master_prompt.json).
#   Apply  checks every prompt, saves the master prompt file and makes the changes live in
#          the running suite - prompts are read fresh on every model call, no restart needed.
#   Load   reads any master prompt file into the editor so you can review it, then Apply.
# Only prompts that differ from the built-in defaults are stored, so a change you make to a
# default in the code still reaches every prompt you haven't customised. Placeholders like
# {topic} and {transcript} are checked so a typo can't crash a turn. New roles / rules /
# templates added to CONFIG later show up in the editor automatically.
# The saved master prompt is loaded over CONFIG at startup (see load_master_prompt below).
# ============================================================================

PROMPT_EDITOR_TITLE = "LMSuite Prompt Editor"
MASTER_PROMPT_PATH = os.path.join(os.path.expanduser("~"), "Downloads", "lm_master_prompt.json")
MASTER_FORMAT = "lmsuite_master_prompt"
MASTER_VERSION = 1

# CONFIG sections that hold prompts, in the order they appear in the editor.
_SCAN_SECTIONS = ("DISCUSSION_ROLES", "DISCUSSION_RULES", "DISCUSSION", "FINAL_WRITER", "OUTPUT_PROFILES")
_FRAGMENT_KEYS = ("form", "create_focus")   # OUTPUT_PROFILES text that is slotted into a template

# Which {placeholders} the code fills in for each kind of template (see the .format() call sites
# in LMengine.py). Anything else inside braces would crash the turn, so it is rejected.
PLACEHOLDERS = {
    "turn_template": {"topic", "transcript"},
    "synthesis_template": {"topic", "transcript"},
    "verify_template": {"topic", "claims_list"},
    "selfcheck_template": {"topic", "claims_list"},
    "crosscheck_template": {"topic", "existing_summary", "claims_list"},
    "followup_template": {"topic", "turn"},
    "compaction_template": {"topic", "full_transcript"},
    "final_writer_template": {"article", "form", "topic", "instructions_block", "knowledge", "create_focus"},
}

USED_BY = {
    "turn_template": "Sent to the model every time this role takes a turn in a Discussion round.",
    "synthesis_template": "Sent to the model for the Moderator's closing synthesis of the whole discussion.",
    "verify_template": "Discussion Rule: the speaker re-reads its own turn and retracts statements it isn't sure about.",
    "selfcheck_template": "Discussion Rule: checks each factual statement against the model's own knowledge.",
    "crosscheck_template": "Discussion Rule: compares the new turn with the discussion so far and flags contradictions.",
    "followup_template": "Discussion Rule: after each turn, asks the speaker for more specific detail on its strongest claim.",
    "compaction_template": "Sent when older turns are folded into the running summary (Auto-Merge / Compact Now).",
    "final_writer_template": "The main prompt for the Final Writer, which turns the whole discussion into the finished piece.",
    "form": "What the Final Writer produces for this Category (e.g. \"short story\"). Slotted into the Final Writer "
            "template as {form}. Plain text only - no curly braces.",
    "create_focus": "Extra instruction the Final Writer gets for this Category. Slotted into the Final Writer "
                    "template as {create_focus}. Plain text only - no curly braces.",
}

# The app parses the reply to these prompts, so the answer format has to stay in the prompt.
FORMAT_NOTES = {
    "verify_template": "The app reads this prompt's reply: keep the instruction to answer one line per statement "
                       "as '<number>. CONFIRM or RETRACT'.",
    "selfcheck_template": "The app reads this prompt's reply: keep the instruction to answer one line per statement "
                          "as '<number>. CONSISTENT or QUESTIONABLE'.",
    "crosscheck_template": "The app reads this prompt's reply: keep the instruction to answer one line per statement "
                           "as '<number>. OK or CONTRADICTS'.",
    "followup_template": "The app treats a reply that starts with NO FURTHER DETAIL as \"nothing to add\": keep "
                         "that instruction.",
    "final_writer_template": "Keep the instruction to return ONLY the finished piece, or any preamble the model "
                             "adds will end up in the Final Output.",
}

# Words the app looks for in the model's reply. If the prompt never mentions them the rule silently stops working.
REQUIRED_WORDS = {
    "verify_template": ["RETRACT"],
    "selfcheck_template": ["QUESTIONABLE"],
    "crosscheck_template": ["CONTRADICTS"],
    "followup_template": ["NO FURTHER DETAIL"],
}

_PE_DEFAULTS = {}            # prompt id -> text as written in the code (captured before any override)
_pe_startup_loaded = False   # has load_master_prompt() already run for the standard file?
_pe_open_editor = None       # the editor window, if one is open


# ============================================================================
# Finding the prompts inside CONFIG
# ============================================================================
def _walk_strings(node, path):
    if isinstance(node, dict):
        for k, v in node.items():
            yield from _walk_strings(v, path + (k,))
    elif isinstance(node, str):
        yield path, node


def _pretty(key):
    return key.replace("_", " ").replace(" template", "").strip().title()


def _make_entry(config, path, key, kind, text):
    section = path[0]
    sub = None
    if section == "DISCUSSION_ROLES":
        group = "Discussion Roles"
        label = path[1] if key == "turn_template" else "%s (%s)" % (path[1], _pretty(key))
    elif section == "DISCUSSION_RULES":
        group = "Discussion Rules"
        rule_cfg = config.get(section, {}).get(path[1], {})
        label = rule_cfg.get("label", path[1]) if isinstance(rule_cfg, dict) else path[1]
    elif section == "DISCUSSION":
        group = "Discussion (general)"
        label = "Compaction summary" if key == "compaction_template" else _pretty(key)
    elif section == "FINAL_WRITER":
        group = "Final Writer"
        label = "Final Writer template" if key == "final_writer_template" else _pretty(key)
    else:  # OUTPUT_PROFILES
        group = "Output Profiles"
        sub = path[1]
        label = "Form (what is produced)" if key == "form" else "Focus (extra instruction)"
    title = " › ".join([group] + ([sub] if sub else []) + [label])
    return {"id": "/".join(path), "path": path, "key": key, "kind": kind, "text": text,
            "group": group, "sub": sub, "label": label, "title": title}


def collect_prompts(config):
    """Every editable prompt currently in CONFIG, as a list of dicts (see _make_entry)."""
    entries = []
    for section in _SCAN_SECTIONS:
        node = config.get(section)
        if not isinstance(node, dict):
            continue
        for path, text in _walk_strings(node, (section,)):
            key = path[-1]
            if section == "OUTPUT_PROFILES":
                if key not in _FRAGMENT_KEYS:
                    continue
                kind = "fragment"
            elif key.endswith("_template"):
                kind = "template"
            else:
                continue
            entries.append(_make_entry(config, path, key, kind, text))
    return entries


def snapshot_defaults(config):
    """Remember each prompt's text as written in the code, before any master prompt is applied."""
    for e in collect_prompts(config):
        _PE_DEFAULTS.setdefault(e["id"], e["text"])


def _set_path(config, path, text):
    node = config
    for k in path[:-1]:
        node = node[k]
    node[path[-1]] = text


# ============================================================================
# Checking a prompt
# ============================================================================
def _parse_fields(text):
    """The set of {names} used in text. Raises ValueError with a readable message."""
    try:
        parsed = list(string.Formatter().parse(text))
    except ValueError as e:
        raise ValueError("Curly-brace problem (%s). Every { needs a matching } and only the listed "
                         "placeholders may go inside braces." % e)
    names = set()
    for _lit, field, _spec, _conv in parsed:
        if field is None:
            continue
        base = re.split(r"[.\[]", field, maxsplit=1)[0]
        if field != base:
            raise ValueError("'{%s}' is not allowed - placeholders can't use attributes or indexes." % field)
        if base == "" or base.isdigit():
            raise ValueError("An empty or numbered placeholder '{%s}' is not allowed - use a named one "
                             "like {topic}." % base)
        names.add(base)
    return names


def allowed_placeholders(key, kind, default_text=""):
    if kind == "fragment":
        return set()
    if key in PLACEHOLDERS:
        return set(PLACEHOLDERS[key])
    try:
        return _parse_fields(default_text) if default_text else set()
    except ValueError:
        return set()


def validate_prompt(key, kind, text, default_text=""):
    """Returns (errors, warnings). Errors block Apply; warnings ask for confirmation."""
    errors, warnings = [], []
    if not text.strip():
        return ["The prompt is empty."], []

    if kind == "fragment":
        if "{" in text or "}" in text:
            errors.append("Curly braces aren't allowed in this field (it is inserted into the Final Writer template).")
        return errors, warnings

    allowed = allowed_placeholders(key, kind, default_text)
    try:
        used = _parse_fields(text)
    except ValueError as e:
        return [str(e)], []

    unknown = sorted(used - allowed)
    if unknown:
        errors.append("Unknown placeholder(s): %s. Allowed here: %s." % (
            ", ".join("{%s}" % n for n in unknown),
            ", ".join("{%s}" % n for n in sorted(allowed)) or "none"))
    if not errors:
        try:
            text.format(**{n: "x" for n in allowed})
        except (KeyError, IndexError, ValueError, AttributeError, TypeError) as e:
            errors.append("This text can't be used as a template (%s)." % e)
    if errors:
        return errors, warnings

    try:
        default_fields = _parse_fields(default_text) if default_text else set()
    except ValueError:
        default_fields = set()
    for n in sorted((default_fields & allowed) - used):
        warnings.append("{%s} is no longer in the prompt, so the model won't be given that part." % n)
    for w in REQUIRED_WORDS.get(key, []):
        if w.lower() not in text.lower():
            warnings.append("The app looks for \"%s\" in the model's reply, but this prompt never asks for it - "
                            "this rule would stop working." % w)
    return errors, warnings


# ============================================================================
# Master prompt file + applying it
# ============================================================================
def read_master_file(path):
    """Return {prompt id: text} from a master prompt file. Raises ValueError with a readable message."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except OSError as e:
        raise ValueError("Couldn't read %s (%s)." % (path, e))
    except json.JSONDecodeError as e:
        raise ValueError("%s isn't valid JSON (%s)." % (os.path.basename(path), e))
    if not isinstance(data, dict):
        raise ValueError("%s isn't a master prompt file." % os.path.basename(path))
    if data.get("format") not in (None, MASTER_FORMAT):
        raise ValueError("%s is a different kind of file (format '%s')." % (os.path.basename(path), data.get("format")))
    prompts = data["prompts"] if "prompts" in data else data
    if not isinstance(prompts, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in prompts.items()):
        raise ValueError("%s doesn't contain a valid set of prompts." % os.path.basename(path))
    return prompts


def save_master_file(prompts, path=None):
    """Write {prompt id: text} to a master prompt file (atomically, keeping one .bak of the old file)."""
    path = path or MASTER_PROMPT_PATH
    data = {
        "format": MASTER_FORMAT,
        "version": MASTER_VERSION,
        "saved_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "note": "Only prompts that differ from the built-in defaults are stored. Edit with the Prompt Editor in LMSuite.",
        "prompts": prompts,
    }
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    if os.path.isfile(path):
        shutil.copy2(path, path + ".bak")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def _pe_resolve(entries, defaults, prompts):
    """Work out the final text for every prompt: the master prompt's text where it is valid,
    otherwise the built-in default. Returns (texts, customised_ids, problems)."""
    known = {e["id"] for e in entries}
    problems = ["'%s' is not a prompt in this version of the suite - ignored." % pid
                for pid in prompts if pid not in known]
    texts, custom = {}, []
    for e in entries:
        pid = e["id"]
        default = defaults.get(pid, e["text"])
        text = default
        if pid in prompts:
            cand = prompts[pid]
            errs = validate_prompt(e["key"], e["kind"], cand, default)[0] if isinstance(cand, str) else ["Not text."]
            if errs:
                problems.append("%s: %s Kept the built-in default." % (e["title"], errs[0]))
            else:
                text = cand
        texts[pid] = text
        if text != default:
            custom.append(pid)
    return texts, custom, problems


def apply_master_prompt(config, prompts):
    """Make CONFIG match {prompt id: text}: those prompts get the master text, every other prompt
    goes back to its built-in default. Returns (customised_ids, problems)."""
    snapshot_defaults(config)
    entries = collect_prompts(config)
    texts, custom, problems = _pe_resolve(entries, _PE_DEFAULTS, prompts or {})
    for e in entries:
        _set_path(config, e["path"], texts[e["id"]])
    return custom, problems


def load_master_prompt(config, path=None):
    """Startup hook: apply the saved master prompt file to CONFIG if it exists.
    Returns (customised_ids, problems); quiet and harmless when there is no file yet."""
    global _pe_startup_loaded
    path = path or MASTER_PROMPT_PATH
    snapshot_defaults(config)
    if os.path.normpath(path) == os.path.normpath(MASTER_PROMPT_PATH):
        _pe_startup_loaded = True
    if not os.path.isfile(path):
        return [], []
    try:
        prompts = read_master_file(path)
    except ValueError as e:
        print("[Prompt Editor] %s" % e, file=sys.stderr)
        return [], [str(e)]
    custom, problems = apply_master_prompt(config, prompts)
    for p in problems:
        print("[Prompt Editor] %s" % p, file=sys.stderr)
    return custom, problems


def ensure_loaded(config):
    """Make sure CONFIG reflects the saved master prompt (covers a missing startup hook)."""
    if not _pe_startup_loaded:
        load_master_prompt(config)


# ============================================================================
# The editor window
# ============================================================================
def _pe_theme(config):
    c = (config or {}).get("COLORS", {})
    f = (config or {}).get("FONTS", {})
    return {
        "bg": c.get("bg_primary", "#f4f5f7"), "card": c.get("bg_white", "#ffffff"),
        "input": c.get("bg_input", "#ffffff"), "text": c.get("text_primary", "#111827"),
        "muted": c.get("text_muted", "#6b7280"), "on_accent": c.get("text_white", "#ffffff"),
        "error": c.get("text_error", "#b91c1c"), "warn": c.get("text_warning", "#b45309"),
        "accent": c.get("button_primary", "#2563eb"), "ok": c.get("button_green", "#15803d"),
        "danger": c.get("button_red", "#b91c1c"), "border": c.get("border_gray", "#d1d5db"),
        "neutral": c.get("tab_inactive", "#e5e7eb"),
        "family": f.get("family_default", "Segoe UI"), "mono": f.get("family_mono", "Consolas"),
        "size": f.get("size_normal", 10), "small": f.get("size_small", 9), "title": f.get("size_title", 11),
    }


class PromptEditor:
    def __init__(self, parent, config, on_apply=None, master_path=None):
        self.config = config
        self.on_apply = on_apply
        self.master_path = master_path or MASTER_PROMPT_PATH
        self.t = _pe_theme(config)

        snapshot_defaults(config)
        self.entries = collect_prompts(config)
        self.by_id = {e["id"]: e for e in self.entries}
        self.defaults = {e["id"]: _PE_DEFAULTS.get(e["id"], e["text"]) for e in self.entries}
        self.baseline = {e["id"]: e["text"] for e in self.entries}   # what is live right now
        self.working = dict(self.baseline)                            # what the editor currently holds
        self.current_id = None
        self._loading = False
        self._rebuilding = False

        self.win = tk.Toplevel(parent)
        self._build_ui()
        self._rebuild_tree()
        if self.entries:
            self._select(self.entries[0]["id"])
        else:
            self._set_status("No prompts were found in CONFIG.", "err")
        self._update_counts()

    # ---- layout ---------------------------------------------------------------------------
    def _btn(self, parent, text, command, kind="primary"):
        t = self.t
        bg = {"primary": t["accent"], "ok": t["ok"], "danger": t["danger"], "neutral": t["neutral"]}[kind]
        fg = t["text"] if kind == "neutral" else t["on_accent"]
        return tk.Button(parent, text=text, command=command, bg=bg, fg=fg, activebackground=bg,
                         activeforeground=fg, relief=tk.FLAT, bd=0, padx=12, pady=6, cursor="hand2",
                         font=(t["family"], t["size"]))

    def _build_ui(self):
        t, win = self.t, self.win
        win.title(PROMPT_EDITOR_TITLE)
        win.geometry("1120x720")
        win.minsize(920, 600)
        win.configure(bg=t["bg"])
        win.protocol("WM_DELETE_WINDOW", self._close)
        win.bind("<Control-s>", lambda _e: self.apply())
        win.bind("<Control-f>", lambda _e: (self.search_entry.focus_set(), "break")[1])

        font = (t["family"], t["size"])
        font_bold = (t["family"], t["size"], "bold")
        font_small = (t["family"], t["small"])

        # Header
        header = tk.Frame(win, bg=t["bg"])
        header.pack(fill=tk.X, padx=14, pady=(12, 6))
        tk.Label(header, text="Prompt Editor", bg=t["bg"], fg=t["text"],
                 font=(t["family"], t["title"] + 4, "bold")).pack(side=tk.LEFT)
        self.count_var = tk.StringVar()
        tk.Label(header, textvariable=self.count_var, bg=t["bg"], fg=t["muted"], font=font_small).pack(side=tk.RIGHT)
        tk.Label(win, text="Master prompt file:  " + self.master_path, bg=t["bg"], fg=t["muted"], font=font_small,
                 anchor=tk.W).pack(fill=tk.X, padx=14)

        # Bottom bar (packed before the body so it always stays visible)
        bottom = tk.Frame(win, bg=t["bg"])
        bottom.pack(side=tk.BOTTOM, fill=tk.X, padx=14, pady=(6, 12))
        self.status_var = tk.StringVar()
        self.status_lbl = tk.Label(bottom, textvariable=self.status_var, bg=t["bg"], fg=t["muted"], font=font_small,
                                   anchor=tk.W, justify=tk.LEFT)
        self.status_lbl.pack(fill=tk.X, pady=(0, 6))
        bar = tk.Frame(bottom, bg=t["bg"])
        bar.pack(fill=tk.X)
        self._btn(bar, "📂 Load Master Prompt…", self.load_file, "neutral").pack(side=tk.LEFT, padx=(0, 6))
        self._btn(bar, "💾 Save As…", self.save_as, "neutral").pack(side=tk.LEFT, padx=(0, 6))
        self._btn(bar, "↺ Reset All to Defaults", self.reset_all, "danger").pack(side=tk.LEFT)
        self._btn(bar, "Close", self._close, "neutral").pack(side=tk.RIGHT)
        self._btn(bar, "✓ Apply  (Ctrl+S)", self.apply, "ok").pack(side=tk.RIGHT, padx=(0, 6))

        # Body: prompt list on the left, editor on the right
        paned = tk.PanedWindow(win, orient=tk.HORIZONTAL, sashwidth=6, bg=t["bg"], bd=0)
        paned.pack(fill=tk.BOTH, expand=True, padx=14, pady=8)

        left = tk.Frame(paned, bg=t["bg"])
        paned.add(left, minsize=260, width=360)
        search_row = tk.Frame(left, bg=t["bg"])
        search_row.pack(fill=tk.X, pady=(0, 6))
        tk.Label(search_row, text="Find:", bg=t["bg"], fg=t["muted"], font=font_small).pack(side=tk.LEFT)
        self.search_var = tk.StringVar()
        self.search_entry = tk.Entry(search_row, textvariable=self.search_var, font=font, relief=tk.SOLID, bd=1,
                                     bg=t["input"], fg=t["text"], insertbackground=t["text"])
        self.search_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(6, 0))
        self.search_var.trace_add("write", lambda *_a: self._rebuild_tree())

        style = ttk.Style(win)
        style.configure("PE.Treeview", background=t["card"], fieldbackground=t["card"], foreground=t["text"],
                        rowheight=t["size"] * 2 + 6, font=font, borderwidth=1)
        style.map("PE.Treeview", background=[("selected", t["accent"])], foreground=[("selected", t["on_accent"])])
        tree_wrap = tk.Frame(left, bg=t["bg"])
        tree_wrap.pack(fill=tk.BOTH, expand=True)
        self.tree = ttk.Treeview(tree_wrap, show="tree", selectmode="browse", style="PE.Treeview")
        tree_sb = ttk.Scrollbar(tree_wrap, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=tree_sb.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        tree_sb.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.tag_configure("custom", foreground=t["accent"])
        self.tree.tag_configure("unsaved", foreground=t["warn"])
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        tk.Label(left, text="●  customised          amber = not applied yet",
                 bg=t["bg"], fg=t["muted"], font=font_small, anchor=tk.W).pack(fill=tk.X, pady=(6, 0))

        right = tk.Frame(paned, bg=t["bg"])
        paned.add(right, minsize=420)
        self.title_lbl = tk.Label(right, text="", bg=t["bg"], fg=t["text"], font=(t["family"], t["title"], "bold"),
                                  anchor=tk.W, justify=tk.LEFT)
        self.title_lbl.pack(fill=tk.X)
        self.used_lbl = tk.Label(right, text="", bg=t["bg"], fg=t["muted"], font=font_small, anchor=tk.W,
                                 justify=tk.LEFT)
        self.used_lbl.pack(fill=tk.X, pady=(2, 0))
        self.note_lbl = tk.Label(right, text="", bg=t["bg"], fg=t["warn"], font=font_small, anchor=tk.W,
                                 justify=tk.LEFT)
        self.note_lbl.pack(fill=tk.X, pady=(2, 0))
        self.ph_frame = tk.Frame(right, bg=t["bg"])
        self.ph_frame.pack(fill=tk.X, pady=(8, 6))

        # The reset button and validation line are packed first (at the bottom) so the expanding text box
        # can never squeeze them out of a short window.
        self._btn(right, "↺ Reset this prompt to default", self.reset_current, "neutral").pack(
            side=tk.BOTTOM, anchor=tk.W, pady=(6, 0))
        self.valid_lbl = tk.Label(right, text="", bg=t["bg"], fg=t["ok"], font=font_bold, anchor=tk.W,
                                  justify=tk.LEFT)
        self.valid_lbl.pack(side=tk.BOTTOM, fill=tk.X, pady=(6, 0))

        text_wrap = tk.Frame(right, bg=t["bg"])
        text_wrap.pack(fill=tk.BOTH, expand=True)
        self.text = tk.Text(text_wrap, wrap=tk.WORD, undo=True, maxundo=-1, font=(t["mono"], t["size"]),
                            bg=t["input"], fg=t["text"], insertbackground=t["text"], relief=tk.SOLID, bd=1,
                            padx=10, pady=8, spacing3=3, height=8)
        text_sb = ttk.Scrollbar(text_wrap, orient=tk.VERTICAL, command=self.text.yview)
        self.text.configure(yscrollcommand=text_sb.set)
        self.text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        text_sb.pack(side=tk.RIGHT, fill=tk.Y)
        self.text.bind("<<Modified>>", self._on_modified)

        def on_right_resize(event):
            wrap = max(200, event.width - 20)
            for lbl in (self.title_lbl, self.used_lbl, self.note_lbl, self.valid_lbl):
                lbl.config(wraplength=wrap)
        right.bind("<Configure>", on_right_resize)

    # ---- tree -------------------------------------------------------------------------------
    def _leaf_text(self, pid):
        mark = "● " if self.working[pid] != self.defaults[pid] else "    "
        return mark + self.by_id[pid]["label"]

    def _leaf_tags(self, pid):
        if self.working[pid] != self.baseline[pid]:
            return ("unsaved",)
        if self.working[pid] != self.defaults[pid]:
            return ("custom",)
        return ()

    def _rebuild_tree(self):
        self._rebuilding = True
        query = self.search_var.get().strip().lower()
        self.tree.delete(*self.tree.get_children())
        groups = {}
        for e in self.entries:
            hay = " ".join([e["group"], e["sub"] or "", e["label"], e["id"]]).lower()
            if query and query not in hay:
                continue
            gid = "g:" + e["group"]
            if gid not in groups:
                self.tree.insert("", "end", iid=gid, text=e["group"], open=True)
                groups[gid] = gid
            parent = gid
            if e["sub"]:
                sid = gid + "/" + e["sub"]
                if sid not in groups:
                    self.tree.insert(gid, "end", iid=sid, text=e["sub"], open=bool(query))
                    groups[sid] = sid
                parent = sid
            self.tree.insert(parent, "end", iid=e["id"], text=self._leaf_text(e["id"]), tags=self._leaf_tags(e["id"]))
        if self.current_id and self.tree.exists(self.current_id):
            self.tree.selection_set(self.current_id)
            self.tree.see(self.current_id)
        self._rebuilding = False

    def _refresh_marker(self, pid):
        if self.tree.exists(pid):
            self.tree.item(pid, text=self._leaf_text(pid), tags=self._leaf_tags(pid))

    def _refresh_all_markers(self):
        for e in self.entries:
            self._refresh_marker(e["id"])
        self._update_counts()

    def _select(self, pid):
        if not self.tree.exists(pid):
            self.search_var.set("")          # rebuilds the tree with everything visible
        if self.tree.exists(pid):
            self.tree.selection_set(pid)
            self.tree.focus(pid)
            self.tree.see(pid)
        if pid != self.current_id:
            self._show(pid)

    def _on_select(self, _event=None):
        if self._rebuilding:
            return
        sel = self.tree.selection()
        if sel and sel[0] in self.by_id and sel[0] != self.current_id:
            self._show(sel[0])

    # ---- showing / editing one prompt ---------------------------------------------------------
    def _show(self, pid):
        self.current_id = pid
        e = self.by_id[pid]
        self.title_lbl.config(text=e["title"])
        self.used_lbl.config(text=USED_BY.get(e["key"], ""))
        self.note_lbl.config(text=FORMAT_NOTES.get(e["key"], ""))
        self._build_placeholder_buttons(e)
        self._set_text(self.working[pid])
        self._validate_current()

    def _set_text(self, value):
        self._loading = True
        self.text.delete("1.0", tk.END)
        self.text.insert("1.0", value)
        self.text.edit_reset()
        self.text.edit_modified(False)
        self._loading = False

    def _build_placeholder_buttons(self, e):
        t = self.t
        for child in self.ph_frame.winfo_children():
            child.destroy()
        allowed = sorted(allowed_placeholders(e["key"], e["kind"], self.defaults[e["id"]]))
        if not allowed:
            tk.Label(self.ph_frame, text="Plain text only - no {placeholders} in this one.", bg=t["bg"],
                     fg=t["muted"], font=(t["family"], t["small"])).pack(side=tk.LEFT)
            return
        tk.Label(self.ph_frame, text="Insert placeholder:", bg=t["bg"], fg=t["muted"],
                 font=(t["family"], t["small"])).pack(side=tk.LEFT, padx=(0, 6))
        for name in allowed:
            tk.Button(self.ph_frame, text="{%s}" % name, command=lambda n=name: self._insert_placeholder(n),
                      bg=t["neutral"], fg=t["text"], relief=tk.FLAT, bd=0, padx=8, pady=2, cursor="hand2",
                      font=(t["mono"], t["small"])).pack(side=tk.LEFT, padx=(0, 4))

    def _insert_placeholder(self, name):
        self.text.insert(tk.INSERT, "{%s}" % name)
        self.text.focus_set()

    def _on_modified(self, _event=None):
        if not self.text.edit_modified():
            return
        self.text.edit_modified(False)
        if self._loading or self.current_id is None:
            return
        self.working[self.current_id] = self.text.get("1.0", "end-1c")
        self._refresh_marker(self.current_id)
        self._validate_current()
        self._update_counts()

    def _validate_current(self):
        t = self.t
        e = self.by_id[self.current_id]
        errors, warnings = validate_prompt(e["key"], e["kind"], self.working[e["id"]], self.defaults[e["id"]])
        if errors:
            self.valid_lbl.config(text="✗ " + " ".join(errors), fg=t["error"])
        elif warnings:
            self.valid_lbl.config(text="⚠ " + " ".join(warnings), fg=t["warn"])
        else:
            self.valid_lbl.config(text="✓ Valid", fg=t["ok"])

    def reset_current(self):
        if self.current_id is None:
            return
        self.working[self.current_id] = self.defaults[self.current_id]
        self._set_text(self.working[self.current_id])
        self._refresh_marker(self.current_id)
        self._validate_current()
        self._update_counts()
        self._set_status("This prompt is back to its built-in default. Press Apply to save that.", "info")

    # ---- status ------------------------------------------------------------------------------
    def _set_status(self, msg, kind="info"):
        color = {"ok": self.t["ok"], "warn": self.t["warn"], "err": self.t["error"]}.get(kind, self.t["muted"])
        self.status_var.set(msg)
        self.status_lbl.config(fg=color)

    def _update_counts(self):
        custom = sum(1 for e in self.entries if self.working[e["id"]] != self.defaults[e["id"]])
        unsaved = sum(1 for e in self.entries if self.working[e["id"]] != self.baseline[e["id"]])
        self.count_var.set("%d prompts  ·  %d customised  ·  %d not applied yet" % (len(self.entries), custom, unsaved))

    # ---- actions ---------------------------------------------------------------------------
    def _collect_valid_overrides(self):
        """{id: text} for every prompt that differs from its default - or None (after telling the
        user why) if something is invalid or the user backs out of a warning."""
        bad, warned = [], []
        for e in self.entries:
            pid = e["id"]
            errors, warnings = validate_prompt(e["key"], e["kind"], self.working[pid], self.defaults[pid])
            if errors:
                bad.append((e, errors[0]))
            elif warnings and self.working[pid] != self.defaults[pid]:
                warned.append((e, warnings[0]))
        if bad:
            self._select(bad[0][0]["id"])
            lines = "\n".join("• %s: %s" % (e["title"], msg) for e, msg in bad[:8])
            more = "\n…and %d more." % (len(bad) - 8) if len(bad) > 8 else ""
            messagebox.showerror(PROMPT_EDITOR_TITLE, "Fix these before applying:\n\n%s%s" % (lines, more), parent=self.win)
            self._set_status("Nothing was saved - %d prompt(s) need fixing." % len(bad), "err")
            return None
        if warned:
            lines = "\n".join("• %s: %s" % (e["title"], msg) for e, msg in warned[:8])
            if not messagebox.askyesno(PROMPT_EDITOR_TITLE, "These prompts look incomplete:\n\n%s\n\nApply anyway?" % lines,
                                       icon="warning", default="no", parent=self.win):
                self._select(warned[0][0]["id"])
                return None
        return {e["id"]: self.working[e["id"]] for e in self.entries if self.working[e["id"]] != self.defaults[e["id"]]}

    def apply(self):
        """Validate, save the master prompt file and make it live. Returns True on success."""
        overrides = self._collect_valid_overrides()
        if overrides is None:
            return False
        try:
            save_master_file(overrides, self.master_path)
        except OSError as e:
            messagebox.showerror(PROMPT_EDITOR_TITLE, "Couldn't save the master prompt file:\n\n%s" % e, parent=self.win)
            self._set_status("Nothing was saved: %s" % e, "err")
            return False
        custom, problems = apply_master_prompt(self.config, overrides)
        self.baseline = dict(self.working)
        self._refresh_all_markers()
        msg = "✓ Applied %d customised prompt(s): saved to the master prompt file and live from the next model call." % len(custom)
        self._set_status(msg, "ok")
        if problems:
            messagebox.showwarning(PROMPT_EDITOR_TITLE, "Applied, with problems:\n\n" + "\n".join(problems[:8]), parent=self.win)
        if self.on_apply:
            try:
                self.on_apply(len(custom))
            except Exception as e:  # a logging hiccup in the host app must never break Apply
                print("[Prompt Editor] on_apply callback failed: %s" % e, file=sys.stderr)
        return True

    def load_file(self):
        if self._has_unapplied() and not messagebox.askyesno(
                PROMPT_EDITOR_TITLE, "You have edits that haven't been applied. Replace them with the file you load?",
                icon="warning", default="no", parent=self.win):
            return
        path = filedialog.askopenfilename(
            parent=self.win, title="Load master prompt", initialdir=os.path.dirname(self.master_path) or None,
            filetypes=[("Master prompt (JSON)", "*.json"), ("All files", "*.*")])
        if not path:
            return
        try:
            prompts = read_master_file(path)
        except ValueError as e:
            messagebox.showerror(PROMPT_EDITOR_TITLE, str(e), parent=self.win)
            return
        texts, custom, problems = _pe_resolve(self.entries, self.defaults, prompts)
        self.working = texts
        if self.current_id:
            self._set_text(self.working[self.current_id])
            self._validate_current()
        self._refresh_all_markers()
        self._set_status("Loaded %d customised prompt(s) from %s. Review them, then press Apply to save and use them."
                         % (len(custom), os.path.basename(path)), "warn" if problems else "info")
        if problems:
            messagebox.showwarning(PROMPT_EDITOR_TITLE, "Loaded, with problems:\n\n" + "\n".join(problems[:8]), parent=self.win)

    def save_as(self):
        overrides = self._collect_valid_overrides()
        if overrides is None:
            return
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        path = filedialog.asksaveasfilename(
            parent=self.win, title="Save master prompt as", defaultextension=".json",
            initialdir=os.path.dirname(self.master_path) or None, initialfile="master_prompt_%s.json" % stamp,
            filetypes=[("Master prompt (JSON)", "*.json"), ("All files", "*.*")])
        if not path:
            return
        try:
            save_master_file(overrides, path)
        except OSError as e:
            messagebox.showerror(PROMPT_EDITOR_TITLE, "Couldn't save:\n\n%s" % e, parent=self.win)
            return
        self._set_status("Saved a copy to %s (the suite keeps using %s until you Apply)."
                         % (path, os.path.basename(self.master_path)), "ok")

    def reset_all(self):
        if not messagebox.askyesno(PROMPT_EDITOR_TITLE, "Put every prompt back to its built-in default?\n\nNothing is saved until "
                                   "you press Apply.", icon="warning", default="no", parent=self.win):
            return
        self.working = dict(self.defaults)
        if self.current_id:
            self._set_text(self.working[self.current_id])
            self._validate_current()
        self._refresh_all_markers()
        self._set_status("All prompts reset to their defaults. Press Apply to save that.", "info")

    def _has_unapplied(self):
        return any(self.working[e["id"]] != self.baseline[e["id"]] for e in self.entries)

    def _close(self):
        global _pe_open_editor
        if self._has_unapplied():
            answer = messagebox.askyesnocancel(
                PROMPT_EDITOR_TITLE, "You have edits that haven't been applied.\n\nYes = Apply them and close\n"
                       "No = throw them away and close\nCancel = keep editing", icon="warning", parent=self.win)
            if answer is None:
                return
            if answer and not self.apply():
                return
        if _pe_open_editor is self:
            _pe_open_editor = None
        self.win.destroy()


def open_prompt_editor(parent, config, on_apply=None, master_path=None):
    """Open (or raise) the Prompt Editor. `config` is LMengine's CONFIG dict; changes are made to it in
    place, so they take effect on the next model call. `on_apply(n)` is called after each Apply."""
    global _pe_open_editor
    if _pe_open_editor is not None:
        try:
            if _pe_open_editor.win.winfo_exists():
                _pe_open_editor.win.deiconify()
                _pe_open_editor.win.lift()
                _pe_open_editor.win.focus_force()
                return _pe_open_editor
        except tk.TclError:
            pass
        _pe_open_editor = None
    ensure_loaded(config)
    _pe_open_editor = PromptEditor(parent, config, on_apply=on_apply, master_path=master_path)
    return _pe_open_editor


# Apply the saved master prompt (written by the Prompt Editor) over the built-in prompts above.
load_master_prompt(CONFIG)

# Auto-Merge dropdown choices on the Discussion card -> rounds between compactions (0 = off)
DISCUSSION_COMPACT_INTERVALS = {
    "Off": 0,
    "Every 5 rounds": 5,
    "Every 10 rounds": 10,
    "Every 15 rounds": 15,
    "Every 20 rounds": 20,
    "Every 25 rounds": 25,
    "Every 50 rounds": 50,
}


# ----------------------------------------------------------------------------
# Token-limit readout for the Constraints Engine card. Parses THIS file's own source so the
# card can show the limits actually written in it (most aren't in CONFIG - they're hardcoded)
# and flag when the file on disk no longer matches what the running app loaded. Handy for
# checking edits made with the built-in Token Tuner (Constraints Engine card).
# ----------------------------------------------------------------------------
TOKEN_LIMIT_FIELDS = [
    ("turn", "Turn"),
    ("moderator", "Moderator"),
    ("rule_default", "Rules"),
    ("rule_followup", "Follow-up"),
    ("compaction", "Compaction"),
]
_SOURCE_PATH = os.path.abspath(__file__)


def _source_mtime():
    try:
        return os.path.getmtime(_SOURCE_PATH)
    except OSError:
        return None


def read_token_limits_from_source(path=None):
    """Return {key: int or None} parsed from the source file, or None if the file can't be
    read (e.g. when running as a compiled .exe)."""
    try:
        with open(path or _SOURCE_PATH, "r", encoding="utf-8") as f:
            text = f.read()
    except Exception:
        return None

    def one(pattern, hay=text, flags=0):
        found = re.findall(pattern, hay, flags)
        return int(found[0]) if len(found) == 1 else None

    out = {
        "turn": one(r"'turn_max_tokens'\s*:\s*(\d+)"),
        "moderator": one(r"'moderator_max_tokens'\s*:\s*(\d+)"),
        "rule_default": one(r"def _discussion_rule_ask\(self,\s*prompt,\s*model_key,\s*log,\s*max_tokens\s*=\s*(\d+)"),
        "rule_followup": one(r"self\._discussion_rule_ask\(\s*prompt,\s*speaker\[[\"']model_key[\"']\],\s*log,\s*max_tokens\s*=\s*(\d+)"),
        "compaction": None,
    }
    fn = re.search(r"^    def _compact_discussion_transcript\b.*?(?=^    def |\Z)", text, re.S | re.M)
    if fn:
        out["compaction"] = one(r"[\"']max_tokens[\"']\s*:\s*(\d+)", fn.group(0))
    return out


# ----------------------------------------------------------------------------
# TOKEN TUNER (built in) - this used to be the separate lmsuite_token_tuner.py. It is opened from
# the Constraints Engine card. It finds the five max_tokens limits in this file's source, lets you
# change them, makes a timestamped backup, patches ONLY those numbers, syntax-checks the result
# before saving, and re-reads the file to confirm. The suite only reads these limits at startup,
# so restart it after applying.
# ----------------------------------------------------------------------------
TOKEN_TUNER_TITLE = "LMsuite Token Tuner"
TOKEN_TUNER_MIN, TOKEN_TUNER_MAX = 50, 32000


def _tt_single_span(text, pattern, flags=0, base=0):
    """Return (start, end, value) of group 2 if the pattern matches exactly once, else None."""
    matches = list(re.finditer(pattern, text, flags))
    if len(matches) != 1:
        return None
    m = matches[0]
    return (base + m.start(2), base + m.end(2), int(m.group(2)))


def _tt_find_turn(text):
    return _tt_single_span(text, r"('turn_max_tokens'\s*:\s*)(\d+)")


def _tt_find_moderator(text):
    return _tt_single_span(text, r"('moderator_max_tokens'\s*:\s*)(\d+)")


def _tt_find_rule_default(text):
    return _tt_single_span(
        text,
        r"(def _discussion_rule_ask\(self,\s*prompt,\s*model_key,\s*log,\s*max_tokens\s*=\s*)(\d+)",
    )


def _tt_find_rule_followup(text):
    return _tt_single_span(
        text,
        r"(self\._discussion_rule_ask\(\s*prompt,\s*speaker\[[\"']model_key[\"']\],\s*log,\s*max_tokens\s*=\s*)(\d+)",
    )


def _tt_find_compaction(text):
    fm = re.search(
        r"^    def _compact_discussion_transcript\b.*?(?=^    def |\Z)", text, re.S | re.M
    )
    if not fm:
        return None
    return _tt_single_span(fm.group(0), r"([\"']max_tokens[\"']\s*:\s*)(\d+)", base=fm.start())


TOKEN_TUNER_FIELDS = [
    {
        "key": "turn",
        "label": "Regular turns",
        "desc": "Researcher, Worldbuilder, Plot Architect, etc. - one reply per turn",
        "find": _tt_find_turn,
    },
    {
        "key": "moderator",
        "label": "Moderator synthesis",
        "desc": "The Moderator's closing summary of the whole discussion",
        "find": _tt_find_moderator,
    },
    {
        "key": "rule_default",
        "label": "Discussion Rules checks",
        "desc": "Default for the extra per-turn rule checks (dedup, fact-check, cross-check)",
        "find": _tt_find_rule_default,
    },
    {
        "key": "rule_followup",
        "label": "Rule follow-up call",
        "desc": "The 'investigate' rule's follow-up question call",
        "find": _tt_find_rule_followup,
    },
    {
        "key": "compaction",
        "label": "Compaction / summary",
        "desc": "Periodic call that folds older turns into the running summary",
        "find": _tt_find_compaction,
    },
]


def _tt_read_text(path):
    # newline="" keeps the file's original line endings (CRLF vs LF) untouched
    with open(path, "r", encoding="utf-8", newline="") as f:
        return f.read()


def _tt_read_values(path):
    """Return {key: int or None} for every field."""
    text = _tt_read_text(path)
    out = {}
    for fld in TOKEN_TUNER_FIELDS:
        span = fld["find"](text)
        out[fld["key"]] = span[2] if span else None
    return out


def _tt_list_backups(path):
    return sorted(glob.glob(path + ".bak_*"))


def _tt_apply_changes(path, new_values):
    """
    Patch the given {key: int} values into the file.
    Returns (changed_keys, backup_path). Raises ValueError with a readable message on any problem.
    """
    text = _tt_read_text(path)
    edits = []
    changed = []
    for fld in TOKEN_TUNER_FIELDS:
        key = fld["key"]
        if key not in new_values:
            continue
        span = fld["find"](text)
        if span is None:
            raise ValueError(
                f"Couldn't find a single, unambiguous spot for '{fld['label']}' in the file. "
                "The suite's code may have changed - nothing was written."
            )
        start, end, old = span
        new = int(new_values[key])
        if new != old:
            edits.append((start, end, str(new)))
            changed.append(key)

    if not edits:
        return [], None

    new_text = text
    for start, end, repl in sorted(edits, reverse=True):
        new_text = new_text[:start] + repl + new_text[end:]

    try:
        compile(new_text, path, "exec")
    except SyntaxError as e:
        raise ValueError(f"Patched file failed the syntax check ({e}). Nothing was written.")

    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = f"{path}.bak_{stamp}"
    shutil.copy2(path, backup)

    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(new_text)

    # Verify by reading back
    check = _tt_read_values(path)
    for key in changed:
        if check.get(key) != int(new_values[key]):
            raise ValueError(
                "Write finished but the values didn't read back correctly. "
                f"Your original is safe in {os.path.basename(backup)}."
            )
    return changed, backup


class LoadingSplash:
    """Splash screen with loading bar shown during startup checks"""
    def __init__(self, root):
        self.root = root
        self.splash = tk.Toplevel(root)
        self.splash.title(CONFIG['TEXT']['app_title'])
        w, h = CONFIG['DIMENSIONS']['splash_width'], CONFIG['DIMENSIONS']['splash_height']
        self.splash.geometry(f"{w}x{h}")
        self.splash.resizable(False, False)
        self.splash.configure(bg=CONFIG['COLORS']['bg_white'])

        # Center on screen
        self.splash.update_idletasks()
        x = (self.splash.winfo_screenwidth() // 2) - (w // 2)
        y = (self.splash.winfo_screenheight() // 2) - (h // 2)
        self.splash.geometry(f"+{x}+{y}")

        # Remove window decorations for cleaner look
        self.splash.attributes('-topmost', True)

        # Content
        font = (CONFIG['FONTS']['family_default'], CONFIG['FONTS']['size_title'], 'bold')
        ttk.Label(self.splash, text=CONFIG['TEXT']['app_title'], font=font).pack(
            pady=CONFIG['DIMENSIONS']['padding_large'])
        ttk.Label(self.splash, text="Checking server...", foreground=CONFIG['COLORS']['text_muted'],
                 font=(CONFIG['FONTS']['family_default'], CONFIG['FONTS']['size_small'])).pack(
            pady=(0, CONFIG['DIMENSIONS']['padding_normal']))

        self.progress = ttk.Progressbar(self.splash, mode='indeterminate', length=300)
        self.progress.pack(pady=CONFIG['DIMENSIONS']['padding_normal'])
        self.progress.start()

    def close(self):
        """Close splash screen"""
        self.progress.stop()
        self.splash.destroy()


class LMStudioOrchestrator:
    def __init__(self, root):
        self.root = root
        self.root.title(CONFIG['TEXT']['app_title'])
        w, h = CONFIG['DIMENSIONS']['window_width'], CONFIG['DIMENSIONS']['window_height']
        self.root.geometry(f"{w}x{h}")
        self.root.resizable(False, False)

        # Window lock state
        self.window_locked = True

        # Connection state (set for real by check_connection(), but referenced
        # before that first runs if anything checks it during startup)
        self.is_connected = False

        # API Config
        self.api_base = CONFIG['API']['base_url']
        self.model_keys = {}
        self.current_model = None
        self.loading_model = False

        # Document settings
        self.max_tokens_var = tk.IntVar(value=CONFIG['DEFAULTS']['max_tokens'])
        self.temperature_var = tk.DoubleVar(value=CONFIG['DEFAULTS']['temperature'])
        self.output_category_var = tk.StringVar(value=CONFIG['OUTPUT_CATEGORIES'][0])
        # Model-call settings (these used to live on the old Story Cascade card)
        self.timeout_var = tk.IntVar(value=CONFIG['API']['timeout_chat'])
        self.writer_temp_var = tk.DoubleVar(value=CONFIG['DEFAULTS']['writer_temperature'])
        self.writer_max_tokens_var = tk.IntVar(value=CONFIG['DEFAULTS']['writer_max_tokens'])
        self.model_display_names = {}      # dropdown text -> LM Studio model key (filled in from LM Studio)
        self.logs_listbox = None

        # Token tracking
        self.session_tokens = 0
        self.reasoning_tokens = 0

        # Logging
        self.error_log = []
        self.log_file_path = os.path.join(os.path.expanduser("~"), "Downloads", "lm_studio_logs.txt")
        self._initialize_log_file()

        # Settings storage
        self.settings = {
            'api_base': CONFIG['API']['base_url'],
            'quick_start_enabled': False,
            'main_window_width': CONFIG['DIMENSIONS']['window_width'],
            'main_window_height': CONFIG['DIMENSIONS']['window_height'],
            'splash_window_width': CONFIG['DIMENSIONS']['splash_width'],
            'splash_window_height': CONFIG['DIMENSIONS']['splash_height'],
            'discussion_compact_interval_label': 'Every 10 rounds',
            'discussion_participants': [],   # [{"model": display_name, "role": role_name}, ...]
            'discussion_rules': {},          # {rule_key: bool, ...} — see CONFIG['DISCUSSION_RULES']
            'final_writer_model': 'None Selected',   # Final Writer dropdown on the Discussion card
            'handoff_auto': True,            # auto-run the Final Writer when a discussion finishes
            'auto_swap_models': True,        # Constraints Engine card toggle: auto load/unload, one model in VRAM at a time (ON by default)
            'auto_swap_default_applied': False,   # one-time flag so old settings files switch to the new default (see load_settings)
        }
        self.settings_file = os.path.join(os.path.expanduser("~"), "Downloads", "lm_studio_settings.txt")
        self.load_settings()

        # Token-limit readout (Constraints Engine card): what this process is running with, plus
        # a snapshot of the file on disk so we can spot a Token Tuner edit that needs a restart.
        self._token_limits_disk = read_token_limits_from_source()
        self._token_limits_mtime = _source_mtime()
        self.token_limits_running = dict(self._token_limits_disk) if self._token_limits_disk \
            else {k: None for k, _ in TOKEN_LIMIT_FIELDS}
        # turn/moderator come from the live CONFIG - the truest picture of what's in use
        self.token_limits_running['turn'] = CONFIG['DISCUSSION']['turn_max_tokens']
        self.token_limits_running['moderator'] = CONFIG['DISCUSSION']['moderator_max_tokens']

        # Constraint Engine
        self.engine = None
        self.engine_loaded = False
        self.engine_enabled_var = tk.BooleanVar(value=True)
        # Auto load/unload (shown on the Constraints Engine card, ON by default): load the model that's
        # about to be used, unload the others. Mirrored into a plain bool because the background
        # worker threads read it, and Tk vars aren't thread-safe.
        self.auto_swap_models_enabled = bool(self.settings.get('auto_swap_models', True))
        self.auto_swap_models_var = tk.BooleanVar(value=self.auto_swap_models_enabled)
        self._model_swap_lock = threading.Lock()   # one swap at a time
        self._init_constraint_engine()

        # Discussion Rules — opt-in checkboxes (see CONFIG['DISCUSSION_RULES']) that add real
        # extra checks to every discussion turn. Created once here so state survives tab
        # switches; restored from settings, defaulting per-rule from CONFIG if never saved.
        # (Settings saved by the old Research Rules are carried over where a rule still exists.)
        saved_rules = dict(self.settings.get('discussion_rules') or {})
        old_rules = self.settings.get('research_rules') or {}
        if not saved_rules and old_rules:
            saved_rules = {k: v for k, v in old_rules.items() if k in CONFIG['DISCUSSION_RULES']}
            if old_rules.get('research_offline'):
                saved_rules['verify_claims'] = True
        self.discussion_rule_vars = {
            rule_key: tk.BooleanVar(value=saved_rules.get(rule_key, rule_cfg.get('default', False)))
            for rule_key, rule_cfg in CONFIG['DISCUSSION_RULES'].items()
        }

        # Discussion mode state — multi-model roundtable: N participants take turns, with a
        # compact / dedup pattern, and the Final Writer when it finishes.
        self.discussion_running = False
        self.discussion_paused = False
        self.discussion_stop_requested = False
        self.discussion_topic_var = tk.StringVar(value="")
        self.discussion_compact_interval_var = tk.StringVar(
            value=self.settings.get('discussion_compact_interval_label', 'Every 10 rounds'))
        self.discussion_participant_rows = []   # [{"frame", "model_var", "role_var", "model_dropdown"}, ...]
        self.discussion_topic = ""              # topic the current transcript/summary belongs to
        self.discussion_transcript = []         # [{"speaker": model_display, "role": role_name, "text": ..., "round": n}, ...]
        self.discussion_summary = ""            # condensed running summary of turns compaction has folded in
        self.discussion_round = 0
        self.discussion_stall_streak = 0
        self.discussion_last_compaction_round = 0
        self.discussion_compacting = False
        self.discussion_transcript_path = None

        # Final Writer state
        self._synth_at_len = 0                  # transcript length when the last Moderator synthesis was written
        self._writer_at_len = -1                # transcript length when the Final Writer last ran (avoids running twice)
        self._writer_running = False
        self.final_output_category = ""
        self.final_writer_var = tk.StringVar(value=self.settings.get('final_writer_model', 'None Selected'))
        self.writer_instructions_var = tk.StringVar(value="")
        self.writer_auto_var = tk.BooleanVar(value=bool(self.settings.get('handoff_auto', True)))

        # Build UI
        self._build_ui()

        # Bind window events
        self.root.bind("<Configure>", self.update_resolution)
        self.root.protocol("WM_DELETE_WINDOW", self.exit_app)

        # Run startup checks
        self.run_startup_checks()
        self.root.after(1000, self.update_system_resources)

    def _init_constraint_engine(self):
        """Initialize constraint engine with default story constraints"""
        try:
            self.engine = ConstraintEngine(name="StoryValidator")

            # Separate constraints (fast, per-item)
            self.engine.add_separate(IsNonEmpty("IsNonEmpty"))
            self.engine.add_separate(NoDoubleSpaces("NoDoubleSpaces"))
            self.engine.add_separate(NoTrailingWhitespace("NoTrailingWhitespace"))
            self.engine.add_separate(CapitalizeFirst("CapitalizeFirst"))
            self.engine.add_separate(MaxLength("MaxLength", max_len=10000))

            # Collective constraints (global, cross-checking)
            self.engine.add_collective(NoContradictions("NoContradictions"))

            self.engine_loaded = True
            sep_count = len(self.engine.separate_constraints)
            col_count = len(self.engine.collective_constraints)
            self.add_log(f"Constraint Engine loaded: {sep_count} separate, {col_count} collective", "INFO")
        except Exception as e:
            self.engine_loaded = False
            self.add_log(f"Constraint Engine failed to load: {str(e)}", "ERROR")

    def open_engine_editor(self):
        """Open the constraint engine GUI editor"""
        def on_apply(new_engine):
            """Callback: GUI editor sends back the updated engine"""
            self.engine = new_engine
            self.engine_loaded = True
            self._update_engine_status_display()

            sep_count = len(self.engine.separate_constraints)
            col_count = len(self.engine.collective_constraints)
            self.add_log(f"Engine updated from editor: {sep_count} separate, {col_count} collective", "INFO")

        editor_window = tk.Toplevel(self.root)
        ConstraintEngineGUI(editor_window, engine=self.engine, on_apply=on_apply)

    def toggle_engine(self):
        """Handle the Constraints Engine enable/disable checkbox"""
        self._update_engine_status_display()
        state = "enabled" if self.engine_enabled_var.get() else "disabled"
        self.add_log(f"Constraint Engine {state}", "INFO")

    def toggle_auto_swap(self):
        """Handle the 'Auto load/unload models' checkbox. ON = only the model in use stays in VRAM
        (it's loaded just before its turn and the others are unloaded). Saved straight away."""
        self.auto_swap_models_enabled = bool(self.auto_swap_models_var.get())
        self.settings['auto_swap_models'] = self.auto_swap_models_enabled
        self._update_model_load_hints()
        state = "ON" if self.auto_swap_models_enabled else "OFF"
        self.add_log(f"Auto load/unload models {state}", "INFO")
        self.save_settings()

    def _update_model_load_hints(self):
        """Refresh the hint next to the Auto load/unload checkbox on the Constraints Engine card"""
        if hasattr(self, 'auto_swap_hint_label'):
            if self.auto_swap_models_var.get():
                self.auto_swap_hint_label.config(
                    text="Loads each model just before it's used and unloads the others — one model "
                         "in VRAM at a time. Adds a load delay whenever the speaker changes.")
            else:
                self.auto_swap_hint_label.config(
                    text="Off — LM Studio loads each model on first use (needs Just-in-Time loading "
                         "enabled in LM Studio) and nothing is unloaded automatically.")

    def _update_engine_status_display(self):
        """Refresh the status label in the Constraints Engine card"""
        if not hasattr(self, 'engine_running_label'):
            return

        if not self.engine_loaded or not self.engine:
            self.engine_running_label.config(text="❌ Not Loaded", foreground=CONFIG['COLORS']['status_offline'])
            return

        sep_count = len(self.engine.separate_constraints)
        col_count = len(self.engine.collective_constraints)

        if self.engine_enabled_var.get():
            self.engine_running_label.config(
                text=f"✅ Engine Running ({sep_count} separate, {col_count} collective)",
                foreground=CONFIG['COLORS']['status_online']
            )
        else:
            self.engine_running_label.config(
                text=f"⏸ Loaded, Disabled ({sep_count} separate, {col_count} collective)",
                foreground=CONFIG['COLORS']['text_muted']
            )

    def _refresh_token_limits_display(self):
        """Show the token limits this session is running, and whether the file on disk agrees."""
        if not hasattr(self, 'token_limits_label'):
            return
        run = self.token_limits_running
        disk = self._token_limits_disk

        def fmt(v):
            return "n/a" if v is None else str(v)

        self.token_limits_label.config(
            text="Token limits: " + " · ".join(f"{name} {fmt(run.get(k))}" for k, name in TOKEN_LIMIT_FIELDS))

        diffs = []
        if disk:
            for k, name in TOKEN_LIMIT_FIELDS:
                if disk.get(k) is not None and disk[k] != run.get(k):
                    diffs.append(f"{name} {fmt(run.get(k))} → {disk[k]}")

        if diffs:
            self.token_limits_status_label.config(
                text="⚠ File on disk has different limits: " + ", ".join(diffs) + " — restart to apply",
                foreground=CONFIG['COLORS']['text_warning'])
        elif disk:
            self.token_limits_status_label.config(
                text="✓ Matches the file on disk", foreground=CONFIG['COLORS']['status_online'])
        else:
            self.token_limits_status_label.config(
                text="(source file not readable - showing built-in values)",
                foreground=CONFIG['COLORS']['text_muted'])

    def _watch_token_limits(self):
        """Cheap poll: re-read the source only when its modification time changes."""
        try:
            mtime = _source_mtime()
            if mtime != self._token_limits_mtime:
                self._token_limits_mtime = mtime
                self._token_limits_disk = read_token_limits_from_source()
                self._refresh_token_limits_display()
        except Exception:
            pass
        self.root.after(2000, self._watch_token_limits)

    def open_engine_config(self):
        """Load a saved constraint engine JSON config and apply it"""
        file_path = filedialog.askopenfilename(
            title="Open Constraint Engine Config",
            initialdir=os.path.expanduser("~/Downloads"),
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")]
        )

        if not file_path:
            return

        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                loaded_config = json.load(f)

            if "separate" not in loaded_config and "collective" not in loaded_config:
                messagebox.showerror("Error", "Invalid config file — missing separate/collective keys")
                return

            self.engine = config_to_engine(loaded_config)
            self.engine_loaded = True
            self._update_engine_status_display()

            sep_count = len(self.engine.separate_constraints)
            col_count = len(self.engine.collective_constraints)

            messagebox.showinfo("Loaded", f"Constraint config loaded:\n{os.path.basename(file_path)}\n\n{sep_count} separate, {col_count} collective constraints")
            self.add_log(f"Constraint config loaded from: {file_path}", "INFO")

        except Exception as e:
            messagebox.showerror("Error", f"Failed to load config:\n{str(e)}")
            self.add_log(f"Error loading constraint config: {str(e)}", "ERROR")

    def open_engine_test(self):
        """Quick 'Test Engine' dialog reachable directly from the Constraints Engine card —
        type text into the input box, run it through whichever engine is actually loaded/running
        on the app right now (self.engine — the same one Discussion uses), and see
        the corrected version plus a per-constraint pass/fail breakdown in the output box. This is
        a live test of the ACTIVE engine; the separate Test section inside Edit Engine tests
        whatever draft config is currently open in the editor, which may not be applied yet."""
        if not self.engine_loaded or not self.engine:
            messagebox.showwarning(CONFIG['TEXT']['popup_warning'], "No constraint engine is loaded yet.")
            return

        font_mono = (CONFIG['FONTS']['family_mono'], CONFIG['FONTS']['size_small'])
        font_header = (CONFIG['FONTS']['family_default'], CONFIG['FONTS']['size_header'], 'bold')
        font_label = (CONFIG['FONTS']['family_default'], CONFIG['FONTS']['size_normal'], 'bold')
        font_body = (CONFIG['FONTS']['family_default'], CONFIG['FONTS']['size_normal'])
        font_small = (CONFIG['FONTS']['family_default'], CONFIG['FONTS']['size_small'])

        win = tk.Toplevel(self.root)
        win.title("Test Constraint Engine")
        win.geometry("700x480")
        win.configure(bg=CONFIG['COLORS']['bg_primary'])

        # Same layout language as the other windows: grey page, one white bordered card
        card = tk.Frame(win, bg=CONFIG['COLORS']['bg_white'], highlightthickness=CONFIG['DIMENSIONS']['card_border_width'],
                        highlightbackground=CONFIG['COLORS']['border_light'],
                        highlightcolor=CONFIG['COLORS']['border_light'])
        card.pack(fill=tk.BOTH, expand=True, padx=CONFIG['DIMENSIONS']['padding_normal'],
                  pady=CONFIG['DIMENSIONS']['padding_normal'])

        header = tk.Frame(card, bg=CONFIG['COLORS']['bg_white'])
        header.pack(fill=tk.X, padx=14, pady=(14, 8))
        tk.Label(header, text="Test Engine", font=font_header,
                bg=CONFIG['COLORS']['bg_white']).pack(side=tk.LEFT)

        sep_count = len(self.engine.separate_constraints)
        col_count = len(self.engine.collective_constraints)
        tk.Label(header, text=f"({sep_count} separate, {col_count} collective — currently active engine)",
                font=font_small, fg=CONFIG['COLORS']['text_muted'],
                bg=CONFIG['COLORS']['bg_white']).pack(side=tk.LEFT, padx=(10, 0))

        test_auto_fix_var = tk.BooleanVar(value=True)
        tk.Checkbutton(header, text="Auto-fix", variable=test_auto_fix_var, font=font_body,
                        bg=CONFIG['COLORS']['bg_white'], activebackground=CONFIG['COLORS']['bg_white']
                        ).pack(side=tk.RIGHT)

        body = tk.Frame(card, bg=CONFIG['COLORS']['bg_white'])
        body.pack(fill=tk.BOTH, expand=True, padx=14, pady=(0, 8))
        body.columnconfigure(0, weight=1)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(1, weight=1)

        tk.Label(body, text="Input:", font=font_label,
                bg=CONFIG['COLORS']['bg_white']).grid(row=0, column=0, sticky="w", pady=(0, 2))
        tk.Label(body, text="Output (corrected version):", font=font_label,
                bg=CONFIG['COLORS']['bg_white']).grid(row=0, column=1, sticky="w", padx=(8, 0), pady=(0, 2))

        input_box = tk.Text(body, height=8, font=font_mono, wrap=tk.WORD, bg=CONFIG['COLORS']['bg_input'],
                            relief=tk.FLAT, bd=0)
        input_box.grid(row=1, column=0, sticky="nsew", padx=(0, 4))
        input_box.insert("1.0", "  hello world.  This is a  test with  double spaces.  ")

        output_box = tk.Text(body, height=8, font=font_mono, wrap=tk.WORD, bg=CONFIG['COLORS']['bg_secondary'],
                             relief=tk.FLAT, bd=0, state=tk.DISABLED)
        output_box.grid(row=1, column=1, sticky="nsew", padx=(4, 0))

        def set_output(text, color):
            output_box.config(state=tk.NORMAL, fg=color)
            output_box.delete("1.0", tk.END)
            output_box.insert("1.0", text)
            output_box.config(state=tk.DISABLED)

        def run_test():
            data = input_box.get("1.0", tk.END).strip()
            if not data:
                set_output("Enter test text first", CONFIG['COLORS']['text_error'])
                return

            auto_fix = test_auto_fix_var.get()
            start = time.time()
            result = self.engine.validate(data, auto_fix=auto_fix)
            elapsed = (time.time() - start) * 1000

            lines = []
            for entry in result.get("log", []):
                name = entry["constraint"]
                phase = "S" if entry.get("phase") == "separate" else "C"
                ms = entry.get("duration_ms", 0)
                if entry.get("fixed"):
                    lines.append(f"\U0001f527 [{phase}] {name}: FIXED ({ms:.2f}ms)")
                elif entry["passed"]:
                    lines.append(f"✓ [{phase}] {name}: passed ({ms:.2f}ms)")
                else:
                    lines.append(f"✗ [{phase}] {name}: FAILED ({ms:.2f}ms)")

            fixes = sum(1 for e in result.get("log", []) if e.get("fixed"))

            if result.get("success"):
                lines.append(f"\n✓ ALL PASSED ({elapsed:.2f}ms, {fixes} fixes)\n")
                lines.append("Corrected output:")
                lines.append(result.get("data", data))
                color = CONFIG['COLORS']['status_online']
            else:
                failed = result.get("failed_at", "unknown")
                lines.append(f"\n✗ FAILED at: {failed} ({elapsed:.2f}ms) — auto-fix couldn't "
                             f"resolve it, or Auto-fix is off\n")
                lines.append("Best available output (unfixed, since validation failed):")
                lines.append(data)
                color = CONFIG['COLORS']['text_error']

            set_output("\n".join(lines), color)

        btn_row = tk.Frame(card, bg=CONFIG['COLORS']['bg_white'])
        btn_row.pack(fill=tk.X, padx=14, pady=(0, 14))
        tk.Button(btn_row, text='▶ Run Test', command=run_test,
                 **BUTTON_STYLES['primary']).pack(side=tk.LEFT)

    def open_token_tuner(self):
        """Built-in Token Tuner (this used to be the separate lmsuite_token_tuner.py). Changes the
        max_tokens caps the Discussion feature uses by patching just those numbers in the suite's
        source file - see the TOKEN TUNER block near the top of this file for how it works."""
        existing = getattr(self, '_token_tuner_win', None)
        if existing is not None and existing.winfo_exists():
            existing.deiconify()
            existing.lift()
            existing.focus_force()
            return

        colors = CONFIG['COLORS']
        family = CONFIG['FONTS']['family_default']
        font_title = (family, CONFIG['FONTS']['size_large'], 'bold')
        font_label = (family, CONFIG['FONTS']['size_normal'], 'bold')
        font_body = (family, CONFIG['FONTS']['size_normal'])
        font_small = (family, CONFIG['FONTS']['size_small'])
        font_small_bold = (family, CONFIG['FONTS']['size_small'], 'bold')
        lo, hi = TOKEN_TUNER_MIN, TOKEN_TUNER_MAX
        frozen = bool(getattr(sys, 'frozen', False))

        # Default target: this very file when running from source. (A compiled .exe can't read its
        # own source, so there you browse to the .py once and the choice is remembered.)
        if os.path.isfile(_SOURCE_PATH):
            initial_path = _SOURCE_PATH
        else:
            saved = self.settings.get('token_tuner_path', '')
            initial_path = saved if saved and os.path.isfile(saved) else ""

        win = tk.Toplevel(self.root)
        self._token_tuner_win = win
        win.title(TOKEN_TUNER_TITLE)
        win.geometry("940x640")
        win.minsize(900, 600)
        win.configure(bg=colors['bg_primary'])

        path_var = tk.StringVar(value=initial_path)
        status_var = tk.StringVar(value="")
        current = {f["key"]: None for f in TOKEN_TUNER_FIELDS}   # values as last read from the file
        vars_ = {f["key"]: tk.StringVar() for f in TOKEN_TUNER_FIELDS}
        cur_labels = {}
        spinboxes = {}

        # ---- helpers ------------------------------------------------------------------
        def set_status(msg, color=None):
            status_var.set(msg)
            status_lbl.configure(fg=color or colors['text_muted'])

        def set_apply_enabled(enabled):
            apply_btn.config(state=tk.NORMAL if enabled else tk.DISABLED,
                             bg=BUTTON_STYLES['primary']['bg'] if enabled else colors['button_disabled'])

        def sync_suite_readout():
            """Refresh the 'Token limits' line on the Constraints Engine card right away."""
            self._token_limits_mtime = _source_mtime()
            self._token_limits_disk = read_token_limits_from_source()
            self._refresh_token_limits_display()

        def parse(key):
            raw = vars_[key].get().strip()
            try:
                val = int(raw)
            except ValueError:
                return None
            return val if lo <= val <= hi else None

        def refresh_marker(key):
            cur = current.get(key)
            lbl = cur_labels[key]
            if cur is None:
                lbl.configure(text="not found in file", fg=colors['text_error'])
                return
            new = parse(key)
            if new is None:
                lbl.configure(text=f"{cur}   (invalid entry)", fg=colors['text_error'])
            elif new != cur:
                lbl.configure(text=f"{cur}   -> will change to {new}", fg=colors['text_warning'])
            else:
                lbl.configure(text=str(cur), fg=colors['text_muted'])

        # ---- actions ------------------------------------------------------------------
        def browse():
            p = filedialog.askopenfilename(
                parent=win, title="Select LMsuite_polished.py",
                filetypes=[("Python files", "*.py"), ("All files", "*.*")],
                initialdir=os.path.dirname(path_var.get()) or os.path.dirname(_SOURCE_PATH),
            )
            if p:
                path_var.set(p)
                reload_file()

        def reload_file():
            path = path_var.get().strip()
            if not path or not os.path.isfile(path):
                for key in current:
                    current[key] = None
                    vars_[key].set("")
                    spinboxes[key].configure(state="disabled")
                    refresh_marker(key)
                set_apply_enabled(False)
                set_status("Pick your LMsuite_polished.py file to begin.", colors['text_muted'])
                return
            try:
                values = _tt_read_values(path)
            except Exception as e:
                set_status(f"Couldn't read the file: {e}", colors['text_error'])
                return

            missing = []
            for fld in TOKEN_TUNER_FIELDS:
                key = fld["key"]
                current[key] = values[key]
                if values[key] is None:
                    vars_[key].set("")
                    spinboxes[key].configure(state="disabled")
                    missing.append(fld["label"])
                else:
                    spinboxes[key].configure(state="normal")
                    vars_[key].set(str(values[key]))
                refresh_marker(key)

            set_apply_enabled(True)
            if path != _SOURCE_PATH and self.settings.get('token_tuner_path') != path:
                self.settings['token_tuner_path'] = path
                self.save_settings()
            backups = len(_tt_list_backups(path))
            if missing:
                set_status("Loaded, but couldn't locate: " + ", ".join(missing) +
                           ". The suite's code may differ from the version this tool expects. "
                           "Those fields are disabled.", colors['text_warning'])
            else:
                set_status(f"Loaded all {len(TOKEN_TUNER_FIELDS)} limits. {backups} backup(s) on disk.",
                           colors['status_online'])

        def reset():
            for key, val in current.items():
                if val is not None:
                    vars_[key].set(str(val))
            set_status("Reset to the values currently in the file.", colors['text_muted'])

        def apply():
            path = path_var.get().strip()
            if not os.path.isfile(path):
                set_status("That file doesn't exist.", colors['text_error'])
                return

            new_values = {}
            for fld in TOKEN_TUNER_FIELDS:
                key = fld["key"]
                if current[key] is None:
                    continue
                val = parse(key)
                if val is None:
                    messagebox.showerror(TOKEN_TUNER_TITLE, f"'{fld['label']}' must be a whole number "
                                                            f"between {lo} and {hi}.", parent=win)
                    return
                new_values[key] = val

            try:
                changed, backup = _tt_apply_changes(path, new_values)
            except Exception as e:
                set_status(str(e), colors['text_error'])
                messagebox.showerror(TOKEN_TUNER_TITLE, str(e), parent=win)
                return

            if not changed:
                set_status("Nothing to change - all values already match the file.", colors['text_muted'])
                return

            reload_file()
            sync_suite_readout()
            names = ", ".join(f["label"] for f in TOKEN_TUNER_FIELDS if f["key"] in changed)
            note = ("Rebuild the .exe from the edited .py for this to take effect." if frozen
                    else "Restart LMsuite for this to take effect.")
            set_status(f"Saved: {names}.  Backup: {os.path.basename(backup)}.  {note}", colors['status_online'])
            self.add_log(f"Token Tuner saved: {names} (backup {os.path.basename(backup)})", "INFO")

        def restore():
            path = path_var.get().strip()
            backups = _tt_list_backups(path) if path else []
            if not backups:
                messagebox.showinfo(TOKEN_TUNER_TITLE, "No backups found next to the suite file.", parent=win)
                return
            latest = backups[-1]
            if not messagebox.askyesno(
                    TOKEN_TUNER_TITLE,
                    f"Replace the current file with this backup?\n\n{os.path.basename(latest)}\n\n"
                    "Any edits made to the suite since that backup will be lost.", parent=win):
                return
            try:
                shutil.copy2(latest, path)
            except Exception as e:
                set_status(f"Restore failed: {e}", colors['text_error'])
                return
            reload_file()
            sync_suite_readout()
            set_status(f"Restored from {os.path.basename(latest)}.", colors['status_online'])
            self.add_log(f"Token Tuner restored from {os.path.basename(latest)}", "INFO")

        # ---- layout -------------------------------------------------------------------
        outer = tk.Frame(win, bg=colors['bg_primary'])
        outer.pack(fill=tk.BOTH, expand=True, padx=18, pady=18)

        tk.Label(outer, text="Discussion token limits", font=font_title, bg=colors['bg_primary'],
                 fg=colors['text_primary']).pack(anchor=tk.W)
        tk.Label(outer, text="Edits the max_tokens caps inside the suite's source file. "
                             "Restart the suite after applying.", font=font_small,
                 bg=colors['bg_primary'], fg=colors['text_muted']).pack(anchor=tk.W, pady=(0, 12))

        file_row = tk.Frame(outer, bg=colors['bg_primary'])
        file_row.pack(fill=tk.X, pady=(0, 12))
        tk.Label(file_row, text="Suite file:", font=font_body, bg=colors['bg_primary'],
                 fg=colors['text_primary']).pack(side=tk.LEFT)
        tk.Entry(file_row, textvariable=path_var, font=font_body).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=8, ipady=4)
        tk.Button(file_row, text="Browse...", command=browse, **BUTTON_STYLES['neutral']).pack(side=tk.LEFT)
        tk.Button(file_row, text="Reload", command=reload_file, **BUTTON_STYLES['neutral']).pack(
            side=tk.LEFT, padx=(6, 0))

        card = tk.Frame(outer, bg=colors['bg_white'], highlightthickness=CONFIG['DIMENSIONS']['card_border_width'],
                        highlightbackground=colors['border_light'], highlightcolor=colors['border_light'])
        card.pack(fill=tk.BOTH, expand=True)
        grid = tk.Frame(card, bg=colors['bg_white'])
        grid.pack(fill=tk.BOTH, expand=True, padx=CONFIG['DIMENSIONS']['padding_large'],
                  pady=CONFIG['DIMENSIONS']['padding_large'])
        grid.columnconfigure(0, weight=1)

        tk.Label(grid, text="LIMIT", bg=colors['bg_white'], fg=colors['text_muted'],
                 font=font_small_bold).grid(row=0, column=0, sticky=tk.W)
        tk.Label(grid, text="NEW VALUE", bg=colors['bg_white'], fg=colors['text_muted'],
                 font=font_small_bold).grid(row=0, column=1, padx=12)
        tk.Label(grid, text="IN FILE NOW", bg=colors['bg_white'], fg=colors['text_muted'],
                 font=font_small_bold).grid(row=0, column=2, sticky=tk.W)

        for i, fld in enumerate(TOKEN_TUNER_FIELDS, start=1):
            key = fld["key"]
            text_frame = tk.Frame(grid, bg=colors['bg_white'])
            text_frame.grid(row=i, column=0, sticky=tk.W, pady=8)
            tk.Label(text_frame, text=fld["label"], font=font_label, bg=colors['bg_white'],
                     fg=colors['text_primary']).pack(anchor=tk.W)
            tk.Label(text_frame, text=fld["desc"], font=font_small, bg=colors['bg_white'],
                     fg=colors['text_muted']).pack(anchor=tk.W)

            sb = ttk.Spinbox(grid, from_=lo, to=hi, increment=50, width=9, textvariable=vars_[key],
                             font=font_body, justify=tk.RIGHT)
            sb.grid(row=i, column=1, padx=12)
            spinboxes[key] = sb

            cur = tk.Label(grid, text="-", bg=colors['bg_white'], fg=colors['text_muted'], font=font_body,
                           width=26, anchor=tk.W)
            cur.grid(row=i, column=2, sticky=tk.W)
            cur_labels[key] = cur

            vars_[key].trace_add("write", lambda *_a, k=key: refresh_marker(k))

        tk.Label(grid, text=f"Allowed range: {lo}-{hi}. Keep values within your model's context window - "
                            "larger caps make each turn slower.", font=font_small, bg=colors['bg_white'],
                 fg=colors['text_muted']).grid(row=len(TOKEN_TUNER_FIELDS) + 1, column=0, columnspan=3,
                                               sticky=tk.W, pady=(12, 0))

        btns = tk.Frame(outer, bg=colors['bg_primary'])
        btns.pack(fill=tk.X, pady=(14, 0))
        apply_btn = tk.Button(btns, text="Apply changes", command=apply, **BUTTON_STYLES['primary'])
        apply_btn.pack(side=tk.LEFT)
        tk.Button(btns, text="Reset to file values", command=reset, **BUTTON_STYLES['neutral']).pack(
            side=tk.LEFT, padx=6)
        tk.Button(btns, text="Restore latest backup", command=restore, **BUTTON_STYLES['neutral']).pack(
            side=tk.LEFT)

        status_lbl = tk.Label(outer, textvariable=status_var, bg=colors['bg_primary'], fg=colors['text_muted'],
                              font=font_small, anchor=tk.W, justify=tk.LEFT, wraplength=870)
        status_lbl.pack(fill=tk.X, pady=(12, 0))

        reload_file()

    def _build_ui(self):
        """Build the main UI"""
        # ===== SHARED LIGHT THEME (applies to every window, incl. the editor and popups) =====
        # ttk widgets are styled once here; plain tk widgets get the same defaults through the Tk
        # option database. Anything a widget sets explicitly still wins over these defaults.
        _colors, _fonts = CONFIG['COLORS'], CONFIG['FONTS']
        _fam, _size = _fonts['family_default'], _fonts['size_normal']
        _body_font = f"{{{_fam}}} {_size}"
        _neutral = BUTTON_STYLES['neutral']
        self.root.configure(bg=_colors['bg_primary'])
        _defaults = {
            '*Font': _body_font,
            '*Toplevel.background': _colors['bg_primary'],
            '*Button.background': _neutral['bg'], '*Button.foreground': _neutral['fg'],
            '*Button.activeBackground': _neutral['activebackground'],
            '*Button.activeForeground': _neutral['activeforeground'],
            '*Button.relief': 'flat', '*Button.borderWidth': 0, '*Button.highlightThickness': 0,
            '*Button.padX': 14, '*Button.padY': 5, '*Button.cursor': 'hand2',
            '*Checkbutton.background': _colors['bg_white'], '*Checkbutton.activeBackground': _colors['bg_white'],
            '*Checkbutton.foreground': _colors['text_primary'], '*Checkbutton.selectColor': _colors['bg_input'],
            '*Checkbutton.highlightThickness': 0, '*Checkbutton.cursor': 'hand2',
            '*Listbox.selectBackground': _colors['tab_active'], '*Listbox.selectForeground': _colors['text_white'],
            '*Menu.background': _colors['bg_white'], '*Menu.foreground': _colors['text_primary'],
            '*Menu.activeBackground': _colors['tab_active'], '*Menu.activeForeground': _colors['text_white'],
            '*Menu.relief': 'flat',
            # dropdown lists that ttk.Combobox pops open
            '*TCombobox*Listbox.font': _body_font, '*TCombobox*Listbox.background': _colors['bg_input'],
            '*TCombobox*Listbox.foreground': _colors['text_primary'],
            '*TCombobox*Listbox.selectBackground': _colors['tab_active'],
            '*TCombobox*Listbox.selectForeground': _colors['text_white'],
        }
        for _kind in ('Entry', 'Text'):
            _defaults.update({
                f'*{_kind}.relief': 'flat', f'*{_kind}.borderWidth': 0,
                f'*{_kind}.highlightThickness': 1, f'*{_kind}.highlightBackground': _colors['border_gray'],
                f'*{_kind}.highlightColor': _colors['tab_active'], f'*{_kind}.background': _colors['bg_input'],
                f'*{_kind}.foreground': _colors['text_primary'], f'*{_kind}.insertBackground': _colors['text_primary'],
                f'*{_kind}.selectBackground': PALETTE['accent_soft'], f'*{_kind}.selectForeground': _colors['text_primary'],
            })
        for _pattern, _value in _defaults.items():
            self.root.option_add(_pattern, _value)

        _style = ttk.Style()
        try:
            _style.theme_use('clam')
        except tk.TclError:
            pass
        _field = dict(fieldbackground=_colors['bg_input'], foreground=_colors['text_primary'],
                      bordercolor=_colors['border_gray'], lightcolor=_colors['bg_input'],
                      darkcolor=_colors['bg_input'], insertcolor=_colors['text_primary'], padding=4)
        _style.configure('TFrame', background=_colors['bg_primary'])
        _style.configure('TLabel', background=_colors['bg_white'], foreground=_colors['text_primary'],
                         font=(_fam, _size))
        _style.configure('TEntry', **_field)
        _style.configure('TCombobox', background=_neutral['bg'], arrowcolor=_colors['text_secondary'], **_field)
        _style.configure('TSpinbox', background=_neutral['bg'], arrowcolor=_colors['text_secondary'], **_field)
        _style.map('TEntry', bordercolor=[('focus', _colors['tab_active'])])
        _style.map('TCombobox', bordercolor=[('focus', _colors['tab_active'])],
                   background=[('active', _neutral['activebackground'])],
                   fieldbackground=[('readonly', _colors['bg_input']), ('disabled', _colors['bg_secondary'])],
                   selectbackground=[('readonly', _colors['bg_input'])],
                   selectforeground=[('readonly', _colors['text_primary'])])
        _style.map('TSpinbox', bordercolor=[('focus', _colors['tab_active'])],
                   background=[('active', _neutral['activebackground'])])
        _style.configure('Vertical.TScrollbar', background=_colors['border_gray'], troughcolor=_colors['bg_primary'],
                         bordercolor=_colors['bg_primary'], lightcolor=_colors['border_gray'],
                         darkcolor=_colors['border_gray'], arrowcolor=_colors['text_muted'], gripcount=0)
        _style.map('Vertical.TScrollbar', background=[('active', _colors['button_disabled']),
                                                      ('pressed', _colors['button_disabled'])])
        _style.configure('Horizontal.TProgressbar', troughcolor=_colors['border_light'],
                         background=_colors['tab_active'], bordercolor=_colors['border_light'],
                         lightcolor=_colors['tab_active'], darkcolor=_colors['tab_active'])

        # ===== TOP TAB MENU BAR =====
        menu_bar = tk.Frame(self.root, bg=CONFIG['COLORS']['bg_menu'], relief=tk.FLAT, bd=0,
                            highlightthickness=1, highlightbackground=CONFIG['COLORS']['border_gray'])
        menu_bar.pack(fill=tk.X, side=tk.TOP)
        menu_bar.grid_rowconfigure(0, weight=1)

        # File Menu button
        font_btn = (CONFIG['FONTS']['family_default'], CONFIG['FONTS']['size_normal'], 'bold')
        file_menu_btn = tk.Button(
            menu_bar, text=CONFIG['TEXT']['btn_file'], font=font_btn,
            bg=CONFIG['COLORS']['tab_inactive'], activebackground=CONFIG['COLORS']['border_gray'],
            relief=tk.FLAT, bd=0, pady=CONFIG['DIMENSIONS']['padding_small'],
            command=self.show_file_menu, justify=tk.CENTER
        )
        file_menu_btn.grid(row=0, column=0, sticky="nsew", padx=3, pady=5)
        menu_bar.grid_columnconfigure(0, weight=0, minsize=50)

        self.tab_buttons = {}
        self.tab_frames = {}
        self.current_tab = "model_control"

        tabs = [("model_control", CONFIG['TEXT']['card_model_loader'] + " hidden, tap to open")]
        for idx, (tab_id, tab_label) in enumerate(tabs, start=1):
            btn = tk.Button(
                menu_bar, text=tab_label, font=font_btn,
                bg=CONFIG['COLORS']['tab_inactive'], activebackground=CONFIG['COLORS']['border_gray'],
                relief=tk.FLAT, bd=0, pady=CONFIG['DIMENSIONS']['padding_small'],
                command=lambda tid=tab_id: self.switch_tab(tid), justify=tk.CENTER
            )
            btn.grid(row=0, column=idx, sticky="nsew", padx=3, pady=5)
            menu_bar.grid_columnconfigure(idx, weight=1)
            self.tab_buttons[tab_id] = btn

        self.update_tab_appearance()

        # ===== MAIN CONTAINER FRAME =====
        self.main_container = tk.Frame(self.root, bg=CONFIG['COLORS']['bg_primary'])
        self.main_container.pack(fill=tk.BOTH, expand=True)

        # ===== TAB 1: MODEL CONTROL =====
        self.model_control_frame = tk.Frame(self.main_container, bg=CONFIG['COLORS']['bg_primary'])
        self.tab_frames["model_control"] = self.model_control_frame

        # Scrollable container
        canvas = tk.Canvas(self.model_control_frame, bg=CONFIG['COLORS']['bg_primary'], highlightthickness=0)
        scrollbar = ttk.Scrollbar(self.model_control_frame, orient="vertical", command=canvas.yview)
        scrollable_frame = ttk.Frame(canvas, padding=CONFIG['DIMENSIONS']['padding_large'])
        scrollable_frame.configure(style='TFrame')

        scrollable_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )

        canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set, bg=CONFIG['COLORS']['bg_primary'])
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        def _on_mousewheel(event):
            try:
                if canvas.winfo_exists():
                    canvas.yview_scroll(int(-1*(event.delta/120)), "units")
            except tk.TclError:
                pass
        canvas.bind("<MouseWheel>", _on_mousewheel)
        scrollable_frame.bind("<MouseWheel>", _on_mousewheel)

        # ===== CONTROL PANEL (Card 1) =====
        control_panel = tk.Frame(
            scrollable_frame, bg=CONFIG['COLORS']['bg_white'], relief=CONFIG['DIMENSIONS']['card_relief'], bd=0,
            highlightthickness=CONFIG['DIMENSIONS']['card_border_width'], highlightbackground=CONFIG['COLORS']['border_light'],
            highlightcolor=CONFIG['COLORS']['border_light']
        )
        control_panel.pack(fill=tk.BOTH, expand=False, padx=CONFIG['DIMENSIONS']['padding_normal'],
                          pady=(0, CONFIG['DIMENSIONS']['padding_large']))

        # Card header
        card_header = tk.Frame(control_panel, bg=CONFIG['COLORS']['bg_white'])
        card_header.pack(fill=tk.X, padx=CONFIG['DIMENSIONS']['padding_large'],
                        pady=(CONFIG['DIMENSIONS']['padding_large'], CONFIG['DIMENSIONS']['padding_normal']))
        font_header = (CONFIG['FONTS']['family_default'], CONFIG['FONTS']['size_header'], 'bold')
        ttk.Label(card_header, text=CONFIG['TEXT']['card_status'], font=font_header,
                 background=CONFIG['COLORS']['bg_white']).pack(anchor=tk.W)

        # Card content
        card_content = tk.Frame(control_panel, bg=CONFIG['COLORS']['bg_white'])
        card_content.pack(fill=tk.BOTH, expand=True, padx=CONFIG['DIMENSIONS']['padding_large'],
                         pady=(0, CONFIG['DIMENSIONS']['padding_large']))

        font_label = (CONFIG['FONTS']['family_default'], CONFIG['FONTS']['size_normal'], 'bold')
        font_body = (CONFIG['FONTS']['family_default'], CONFIG['FONTS']['size_normal'])
        font_small = (CONFIG['FONTS']['family_default'], CONFIG['FONTS']['size_small'])

        # Status row
        ttk.Label(card_content, text=CONFIG['TEXT']['label_lm_studio_status'], font=font_label,
                 background=CONFIG['COLORS']['bg_white']).pack(anchor=tk.W, pady=(0, CONFIG['DIMENSIONS']['padding_tiny']))

        status_row = tk.Frame(card_content, bg=CONFIG['COLORS']['bg_white'])
        status_row.pack(fill=tk.X, pady=(0, CONFIG['DIMENSIONS']['padding_tiny']))

        self.status_label = tk.Label(status_row, text=CONFIG['TEXT']['status_disconnected'],
                                     foreground=CONFIG['COLORS']['status_offline'], font=font_body,
                                     bg=CONFIG['COLORS']['bg_white'])
        self.status_label.pack(side=tk.LEFT, padx=(0, 15))

        tk.Label(status_row, text=CONFIG['TEXT']['label_server'], font=font_body,
                bg=CONFIG['COLORS']['bg_white']).pack(side=tk.LEFT, padx=(0, 5))
        self.server_label = tk.Label(status_row, text=self.api_base, foreground=CONFIG['COLORS']['text_blue'],
                                     font=(CONFIG['FONTS']['family_mono'], CONFIG['FONTS']['size_small']),
                                     bg=CONFIG['COLORS']['bg_white'])
        self.server_label.pack(side=tk.LEFT)

        # Resolution display with lock button
        resolution_frame = tk.Frame(status_row, bg=CONFIG['COLORS']['bg_white'])
        resolution_frame.pack(side=tk.RIGHT)

        self.resolution_label = tk.Label(resolution_frame, text="491x624",
                                         foreground=CONFIG['COLORS']['text_blue'],
                                         font=(CONFIG['FONTS']['family_mono'], CONFIG['FONTS']['size_small']),
                                         bg=CONFIG['COLORS']['bg_white'])
        self.resolution_label.pack(side=tk.LEFT, padx=(0, 5))

        self.lock_button = tk.Button(resolution_frame, text="🔒",
                                     font=(CONFIG['FONTS']['family_default'], CONFIG['FONTS']['size_tiny']),
                                     command=self.toggle_window_lock, relief=tk.FLAT, bd=0, padx=3, pady=0,
                                     bg=CONFIG['COLORS']['bg_white'],
                                     activebackground=CONFIG['COLORS']['tab_inactive'])
        self.lock_button.pack(side=tk.LEFT)

        # LMs available row
        lms_row = tk.Frame(card_content, bg=CONFIG['COLORS']['bg_white'])
        lms_row.pack(anchor=tk.W, pady=(0, CONFIG['DIMENSIONS']['padding_normal']))

        tk.Label(lms_row, text=CONFIG['TEXT']['label_available_models'], font=font_body,
                bg=CONFIG['COLORS']['bg_white']).pack(side=tk.LEFT, padx=(0, 5))
        self.lms_count_label = tk.Label(lms_row, text="0", foreground=CONFIG['COLORS']['text_blue'],
                                        font=(CONFIG['FONTS']['family_mono'], CONFIG['FONTS']['size_small'], 'bold'),
                                        bg=CONFIG['COLORS']['bg_white'])
        self.lms_count_label.pack(side=tk.LEFT)

        # Token tracking
        token_row = tk.Frame(card_content, bg=CONFIG['COLORS']['bg_white'])
        token_row.pack(anchor=tk.W, pady=(0, CONFIG['DIMENSIONS']['padding_normal']))

        tk.Label(token_row, text=CONFIG['TEXT']['label_tokens_used'], font=font_body,
                bg=CONFIG['COLORS']['bg_white']).pack(side=tk.LEFT, padx=(0, 10))
        self.session_tokens_label = tk.Label(token_row, text="0", foreground=CONFIG['COLORS']['status_online'],
                                             font=(CONFIG['FONTS']['family_mono'], CONFIG['FONTS']['size_small'], 'bold'),
                                             bg=CONFIG['COLORS']['bg_white'])
        self.session_tokens_label.pack(side=tk.LEFT, padx=(0, 20))

        tk.Label(token_row, text=CONFIG['TEXT']['label_reasoning'], font=font_body,
                bg=CONFIG['COLORS']['bg_white']).pack(side=tk.LEFT, padx=(0, 10))
        self.reasoning_tokens_label = tk.Label(token_row, text="0", foreground=CONFIG['COLORS']['text_blue'],
                                               font=(CONFIG['FONTS']['family_mono'], CONFIG['FONTS']['size_small'], 'bold'),
                                               bg=CONFIG['COLORS']['bg_white'])
        self.reasoning_tokens_label.pack(side=tk.LEFT)

        tk.Button(card_content, text=CONFIG['TEXT']['btn_refresh'], command=self.refresh_script,
                 **BUTTON_STYLES['orange']).pack(fill=tk.X, pady=CONFIG['DIMENSIONS']['padding_tiny'])
        tk.Button(card_content, text=CONFIG['TEXT']['btn_restart'], command=self.restart_script,
                 **BUTTON_STYLES['danger']).pack(fill=tk.X, pady=CONFIG['DIMENSIONS']['padding_tiny'])

        # ===== CONSTRAINTS ENGINE (Card 1.5) =====
        engine_card = tk.Frame(
            scrollable_frame, bg=CONFIG['COLORS']['bg_white'], relief=CONFIG['DIMENSIONS']['card_relief'], bd=0,
            highlightthickness=CONFIG['DIMENSIONS']['card_border_width'], highlightbackground=CONFIG['COLORS']['border_light'],
            highlightcolor=CONFIG['COLORS']['border_light']
        )
        engine_card.pack(fill=tk.BOTH, expand=False, padx=CONFIG['DIMENSIONS']['padding_normal'],
                        pady=(0, CONFIG['DIMENSIONS']['padding_large']))

        # Card header: title + running status, right-aligned (same pattern as the other cards)
        engine_card_header = tk.Frame(engine_card, bg=CONFIG['COLORS']['bg_white'])
        engine_card_header.pack(fill=tk.X, padx=CONFIG['DIMENSIONS']['padding_large'],
                                pady=(CONFIG['DIMENSIONS']['padding_large'], CONFIG['DIMENSIONS']['padding_normal']))

        tk.Label(engine_card_header, text="Constraints Engine", font=font_header,
                background=CONFIG['COLORS']['bg_white']).pack(anchor=tk.W, side=tk.LEFT)

        self.engine_running_label = tk.Label(
            engine_card_header, text="❌ Not Loaded",
            font=(CONFIG['FONTS']['family_mono'], CONFIG['FONTS']['size_small'], 'bold'),
            background=CONFIG['COLORS']['bg_white'], foreground=CONFIG['COLORS']['status_offline']
        )
        self.engine_running_label.pack(anchor=tk.E, side=tk.RIGHT)

        # Card content
        engine_card_content = tk.Frame(engine_card, bg=CONFIG['COLORS']['bg_white'])
        engine_card_content.pack(fill=tk.BOTH, expand=True, padx=CONFIG['DIMENSIONS']['padding_large'],
                                 pady=(0, CONFIG['DIMENSIONS']['padding_large']))

        engine_controls_row = tk.Frame(engine_card_content, bg=CONFIG['COLORS']['bg_white'])
        engine_controls_row.pack(fill=tk.X)

        tk.Checkbutton(engine_controls_row, text="Enabled", variable=self.engine_enabled_var,
                        command=self.toggle_engine, font=font_body, bg=CONFIG['COLORS']['bg_white'],
                        fg=CONFIG['COLORS']['text_primary'], selectcolor=CONFIG['COLORS']['bg_input'],
                        activebackground=CONFIG['COLORS']['bg_white']).pack(side=tk.LEFT, padx=(0, 15))

        tk.Button(engine_controls_row, text='📂 Open Config', command=self.open_engine_config,
                 **BUTTON_STYLES['primary']).pack(side=tk.LEFT, padx=(0, 6))

        tk.Button(engine_controls_row, text='⚙️ Edit Engine', command=self.open_engine_editor,
                 **BUTTON_STYLES['primary']).pack(side=tk.LEFT, padx=(0, 6))

        tk.Button(engine_controls_row, text='🧪 Test Engine', command=self.open_engine_test,
                 **BUTTON_STYLES['primary']).pack(side=tk.LEFT, padx=(0, 6))

        tk.Button(engine_controls_row, text='🎚️ Token Tuner', command=self.open_token_tuner,
                 **BUTTON_STYLES['primary']).pack(side=tk.LEFT)

        tk.Button(engine_controls_row, text='📝 Prompt Editor',
                  command=lambda: open_prompt_editor(
                      self.root, CONFIG,
                      on_apply=lambda n: self.add_log(
                          f"Prompt Editor applied: {n} customised prompt(s)", "INFO")),
                  **BUTTON_STYLES['primary']).pack(side=tk.LEFT, padx=(6, 0))

        # Model loading toggle — auto load/unload (one model in VRAM at a time), ON by default
        hint_font = (CONFIG['FONTS']['family_mono'], CONFIG['FONTS']['size_small'])
        check_kw = dict(font=font_body, bg=CONFIG['COLORS']['bg_white'], fg=CONFIG['COLORS']['text_primary'],
                        selectcolor=CONFIG['COLORS']['bg_input'], activebackground=CONFIG['COLORS']['bg_white'],
                        disabledforeground=CONFIG['COLORS']['text_muted'])

        auto_swap_row = tk.Frame(engine_card_content, bg=CONFIG['COLORS']['bg_white'])
        auto_swap_row.pack(fill=tk.X, pady=(8, 0))
        tk.Checkbutton(auto_swap_row, text="Auto load/unload models", variable=self.auto_swap_models_var,
                       command=self.toggle_auto_swap, **check_kw).pack(side=tk.LEFT, padx=(0, 10))
        self.auto_swap_hint_label = tk.Label(
            auto_swap_row, text="", bg=CONFIG['COLORS']['bg_white'], fg=CONFIG['COLORS']['text_muted'],
            font=hint_font, anchor=tk.W, justify=tk.LEFT, wraplength=520)
        self.auto_swap_hint_label.pack(side=tk.LEFT, fill=tk.X, expand=True)

        self._update_model_load_hints()

        # Token limits readout - running values, plus a check against the file on disk
        token_limits_frame = tk.Frame(engine_card_content, bg=CONFIG['COLORS']['bg_white'])
        token_limits_frame.pack(fill=tk.X, pady=(8, 0))
        token_font = (CONFIG['FONTS']['family_mono'], CONFIG['FONTS']['size_small'])
        self.token_limits_label = tk.Label(
            token_limits_frame, text="", font=token_font, bg=CONFIG['COLORS']['bg_white'],
            fg=CONFIG['COLORS']['text_secondary'], anchor=tk.W, justify=tk.LEFT, wraplength=700)
        self.token_limits_label.pack(anchor=tk.W)
        self.token_limits_status_label = tk.Label(
            token_limits_frame, text="", font=token_font, bg=CONFIG['COLORS']['bg_white'],
            fg=CONFIG['COLORS']['text_muted'], anchor=tk.W, justify=tk.LEFT, wraplength=700)
        self.token_limits_status_label.pack(anchor=tk.W)
        self._refresh_token_limits_display()
        self.root.after(2000, self._watch_token_limits)

        self._update_engine_status_display()

        # ===== DISCUSSION MODE (Card 2) — multi-model roundtable (the Final Writer has its own card below) =====
        discussion_card = tk.Frame(
            scrollable_frame, bg=CONFIG['COLORS']['bg_white'], relief=CONFIG['DIMENSIONS']['card_relief'], bd=0,
            highlightthickness=CONFIG['DIMENSIONS']['card_border_width'], highlightbackground=CONFIG['COLORS']['border_light'],
            highlightcolor=CONFIG['COLORS']['border_light']
        )
        discussion_card.pack(fill=tk.BOTH, expand=False, padx=CONFIG['DIMENSIONS']['padding_normal'],
                             pady=(0, CONFIG['DIMENSIONS']['padding_large']))

        # Card header: title + live status, right-aligned (same pattern as the other cards)
        discussion_header = tk.Frame(discussion_card, bg=CONFIG['COLORS']['bg_white'])
        discussion_header.pack(fill=tk.X, padx=CONFIG['DIMENSIONS']['padding_large'],
                               pady=(CONFIG['DIMENSIONS']['padding_large'], CONFIG['DIMENSIONS']['padding_normal']))

        tk.Label(discussion_header, text=CONFIG['TEXT']['card_discussion'], font=font_header,
                background=CONFIG['COLORS']['bg_white']).pack(anchor=tk.W, side=tk.LEFT)

        self.discussion_status_label = tk.Label(
            discussion_header, text=f"{CONFIG['TEXT']['status_discussion_stopped']} — Round 0 · 0 turns",
            font=(CONFIG['FONTS']['family_mono'], CONFIG['FONTS']['size_small'], 'bold'),
            background=CONFIG['COLORS']['bg_white'], foreground=CONFIG['COLORS']['status_offline']
        )
        self.discussion_status_label.pack(anchor=tk.E, side=tk.RIGHT)

        # Card content
        discussion_content = tk.Frame(discussion_card, bg=CONFIG['COLORS']['bg_white'])
        discussion_content.pack(fill=tk.BOTH, expand=True, padx=CONFIG['DIMENSIONS']['padding_large'],
                                pady=(0, CONFIG['DIMENSIONS']['padding_large']))

        # Topic input
        tk.Label(discussion_content, text=CONFIG['TEXT']['label_discussion_topic'], font=font_label,
                bg=CONFIG['COLORS']['bg_white']).pack(anchor=tk.W, pady=(0, CONFIG['DIMENSIONS']['padding_tiny']))

        discussion_topic_entry = tk.Entry(discussion_content, textvariable=self.discussion_topic_var, font=font_body,
                                          bg=CONFIG['COLORS']['bg_input'])
        discussion_topic_entry.pack(fill=tk.X, pady=(0, CONFIG['DIMENSIONS']['padding_normal']), ipady=4)

        # Participants label + dynamic row container
        tk.Label(discussion_content, text=CONFIG['TEXT']['label_discussion_participants'], font=font_label,
                bg=CONFIG['COLORS']['bg_white']).pack(anchor=tk.W, pady=(0, CONFIG['DIMENSIONS']['padding_small']))

        self.discussion_participants_frame = tk.Frame(discussion_content, bg=CONFIG['COLORS']['bg_white'])
        self.discussion_participants_frame.pack(fill=tk.X, pady=(0, CONFIG['DIMENSIONS']['padding_small']))

        tk.Button(discussion_content, text=CONFIG['TEXT']['btn_add_participant'],
                 command=self.add_discussion_participant_row,
                 **BUTTON_STYLES['neutral']).pack(
            anchor=tk.W, pady=(0, CONFIG['DIMENSIONS']['padding_normal']))

        # Auto-Merge interval
        d_merge_row = tk.Frame(discussion_content, bg=CONFIG['COLORS']['bg_white'])
        d_merge_row.pack(fill=tk.X, pady=(0, CONFIG['DIMENSIONS']['padding_normal']))
        tk.Label(d_merge_row, text=CONFIG['TEXT']['label_discussion_auto_merge'], font=font_label,
                bg=CONFIG['COLORS']['bg_white']).pack(side=tk.LEFT, padx=(0, 8))
        self.discussion_compact_dropdown = ttk.Combobox(d_merge_row, textvariable=self.discussion_compact_interval_var,
                                                         state="readonly", width=16,
                                                         values=list(DISCUSSION_COMPACT_INTERVALS.keys()))
        self.discussion_compact_dropdown.pack(side=tk.LEFT)

        # Server timeout - applies to every model call (turns, rules, compaction and the Final Writer)
        timeout_row = tk.Frame(discussion_content, bg=CONFIG['COLORS']['bg_white'])
        timeout_row.pack(fill=tk.X, pady=(0, CONFIG['DIMENSIONS']['padding_small']))
        tk.Label(timeout_row, text=CONFIG['TEXT']['label_server_timeout'], font=font_body,
                bg=CONFIG['COLORS']['bg_white']).pack(side=tk.LEFT, padx=(0, 5))
        ttk.Spinbox(timeout_row, from_=30, to=600, increment=30, textvariable=self.timeout_var,
                   width=6).pack(side=tk.LEFT, padx=(0, 5))
        tk.Label(timeout_row, text='(30-600 seconds)', font=font_small,
                foreground=CONFIG['COLORS']['text_muted'], bg=CONFIG['COLORS']['bg_white']).pack(side=tk.LEFT)

        # Discussion Rules — functional checkboxes (moved over from the old Research card), each
        # wired to a real extra step in _apply_discussion_rules / _discussion_loop (see
        # CONFIG['DISCUSSION_RULES']). Not placeholders: ticking one changes what actually
        # happens on the next turn.
        tk.Label(discussion_content, text=CONFIG['TEXT']['label_discussion_rules'], font=font_label,
                bg=CONFIG['COLORS']['bg_white']).pack(anchor=tk.W, pady=(4, CONFIG['DIMENSIONS']['padding_tiny']))

        d_rules_frame = tk.Frame(discussion_content, bg=CONFIG['COLORS']['bg_white'])
        d_rules_frame.pack(fill=tk.X, pady=(0, CONFIG['DIMENSIONS']['padding_normal']))
        d_rules_frame.grid_columnconfigure(0, weight=1, uniform="d_rule_col")
        d_rules_frame.grid_columnconfigure(1, weight=1, uniform="d_rule_col")

        font_rule_desc = font_small

        for i, (rule_key, rule_cfg) in enumerate(CONFIG['DISCUSSION_RULES'].items()):
            cell = tk.Frame(d_rules_frame, bg=CONFIG['COLORS']['bg_white'])
            cell.grid(row=i // 2, column=i % 2, sticky="w", padx=(0, 12), pady=(0, 4))

            tk.Checkbutton(
                cell, text=rule_cfg['label'], variable=self.discussion_rule_vars[rule_key],
                bg=CONFIG['COLORS']['bg_white'], font=font_body, anchor=tk.W,
                command=self.save_settings
            ).pack(anchor=tk.W)

            tk.Label(cell, text=rule_cfg.get('desc', ''), font=font_rule_desc, wraplength=260,
                    justify=tk.LEFT, fg=CONFIG['COLORS']['text_muted'],
                    bg=CONFIG['COLORS']['bg_white']).pack(anchor=tk.W, padx=(20, 0))

        # Control buttons
        discussion_btn_row = tk.Frame(discussion_content, bg=CONFIG['COLORS']['bg_white'])
        discussion_btn_row.pack(fill=tk.X, pady=(0, CONFIG['DIMENSIONS']['padding_normal']))

        tk.Button(discussion_btn_row, text=CONFIG['TEXT']['btn_discussion_start'], command=self.start_discussion,
                 **BUTTON_STYLES['success']).pack(side=tk.LEFT, padx=(0, 6))

        self.pause_discussion_btn = tk.Button(discussion_btn_row, text=CONFIG['TEXT']['btn_discussion_pause'],
                                              command=self.pause_discussion, **BUTTON_STYLES['neutral'])
        self.pause_discussion_btn.pack(side=tk.LEFT, padx=(0, 6))

        tk.Button(discussion_btn_row, text=CONFIG['TEXT']['btn_discussion_stop'], command=self.stop_discussion,
                 **BUTTON_STYLES['danger']).pack(side=tk.LEFT, padx=(0, 6))

        tk.Button(discussion_btn_row, text=CONFIG['TEXT']['btn_compact_discussion_now'],
                 command=self.compact_discussion_now,
                 **BUTTON_STYLES['neutral']).pack(side=tk.LEFT, padx=(0, 6))

        tk.Button(discussion_btn_row, text=CONFIG['TEXT']['btn_open_transcript'], command=self.open_discussion_transcript,
                 **BUTTON_STYLES['neutral']).pack(side=tk.LEFT)

        # Live discussion log
        tk.Label(discussion_content, text=CONFIG['TEXT']['label_discussion_log'], font=font_label,
                bg=CONFIG['COLORS']['bg_white']).pack(anchor=tk.W, pady=(0, CONFIG['DIMENSIONS']['padding_small']))

        discussion_output_frame = tk.Frame(discussion_content, bg=CONFIG['COLORS']['bg_white'], relief=tk.FLAT, bd=0)
        discussion_output_frame.pack(fill=tk.BOTH, expand=False)

        self.discussion_output = tk.Text(discussion_output_frame, height=14,
                                         font=(CONFIG['FONTS']['family_mono'], CONFIG['FONTS']['size_small']),
                                         wrap=tk.WORD, bg=CONFIG['COLORS']['bg_secondary'],
                                         fg=CONFIG['COLORS']['text_primary'], relief=tk.FLAT, bd=0, state=tk.DISABLED)
        self.discussion_output.pack(fill=tk.BOTH, expand=True)

        # ===== FINAL WRITER MODE (Card 3) - turns the finished discussion into the piece =====
        # Its own card, so the Discussion controls above (Start / Pause / Stop ...) and the Final
        # Writer controls below never get mixed up. Workflow: run the Discussion, then the Final
        # Writer writes the piece (automatically if Auto-write is ticked, or via Write Final Piece).
        writer_card = tk.Frame(
            scrollable_frame, bg=CONFIG['COLORS']['bg_white'], relief=CONFIG['DIMENSIONS']['card_relief'], bd=0,
            highlightthickness=CONFIG['DIMENSIONS']['card_border_width'], highlightbackground=CONFIG['COLORS']['border_light'],
            highlightcolor=CONFIG['COLORS']['border_light']
        )
        writer_card.pack(fill=tk.BOTH, expand=False, padx=CONFIG['DIMENSIONS']['padding_normal'],
                         pady=(0, CONFIG['DIMENSIONS']['padding_large']))

        writer_header = tk.Frame(writer_card, bg=CONFIG['COLORS']['bg_white'])
        writer_header.pack(fill=tk.X, padx=CONFIG['DIMENSIONS']['padding_large'],
                           pady=(CONFIG['DIMENSIONS']['padding_large'], CONFIG['DIMENSIONS']['padding_small']))
        tk.Label(writer_header, text=CONFIG['TEXT']['card_final_writer'], font=font_header,
                background=CONFIG['COLORS']['bg_white']).pack(anchor=tk.W, side=tk.LEFT)

        writer_content = tk.Frame(writer_card, bg=CONFIG['COLORS']['bg_white'])
        writer_content.pack(fill=tk.BOTH, expand=True, padx=CONFIG['DIMENSIONS']['padding_large'],
                            pady=(0, CONFIG['DIMENSIONS']['padding_large']))

        tk.Label(writer_content, text=CONFIG['TEXT']['hint_final_writer'], font=font_small,
                fg=CONFIG['COLORS']['text_muted'], bg=CONFIG['COLORS']['bg_white'], anchor=tk.W,
                justify=tk.LEFT, wraplength=700).pack(anchor=tk.W, pady=(0, CONFIG['DIMENSIONS']['padding_normal']))

        writer_row = tk.Frame(writer_content, bg=CONFIG['COLORS']['bg_white'])
        writer_row.pack(fill=tk.X, pady=(0, CONFIG['DIMENSIONS']['padding_small']))
        tk.Label(writer_row, text=CONFIG['TEXT']['label_final_writer'], font=font_body,
                bg=CONFIG['COLORS']['bg_white']).pack(side=tk.LEFT, padx=(0, 6))
        self.final_writer_dropdown = ttk.Combobox(writer_row, textvariable=self.final_writer_var,
                                                   state="readonly", width=CONFIG['DEFAULTS']['model_display_width'])
        self.final_writer_dropdown.pack(side=tk.LEFT)

        category_row = tk.Frame(writer_content, bg=CONFIG['COLORS']['bg_white'])
        category_row.pack(fill=tk.X, pady=(0, CONFIG['DIMENSIONS']['padding_small']))
        tk.Label(category_row, text=CONFIG['TEXT']['label_output_category'], font=font_body,
                bg=CONFIG['COLORS']['bg_white']).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Combobox(category_row, textvariable=self.output_category_var, values=CONFIG['OUTPUT_CATEGORIES'],
                    state="readonly", width=15).pack(side=tk.LEFT)

        instructions_row = tk.Frame(writer_content, bg=CONFIG['COLORS']['bg_white'])
        instructions_row.pack(fill=tk.X, pady=(0, CONFIG['DIMENSIONS']['padding_small']))
        tk.Label(instructions_row, text=CONFIG['TEXT']['label_writer_instructions'], font=font_body,
                bg=CONFIG['COLORS']['bg_white']).pack(side=tk.LEFT, padx=(0, 6))
        tk.Entry(instructions_row, textvariable=self.writer_instructions_var, font=font_body,
                bg=CONFIG['COLORS']['bg_input']).pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=3)

        # Model-call settings (these used to live on the Story Cascade card)
        limits_row = tk.Frame(writer_content, bg=CONFIG['COLORS']['bg_white'])
        limits_row.pack(fill=tk.X, pady=(0, CONFIG['DIMENSIONS']['padding_small']))
        tk.Label(limits_row, text=CONFIG['TEXT']['label_writer_temperature'], font=font_body,
                bg=CONFIG['COLORS']['bg_white']).pack(side=tk.LEFT, padx=(0, 5))
        ttk.Spinbox(limits_row, from_=0.0, to=2.0, increment=0.1, textvariable=self.writer_temp_var,
                   width=6).pack(side=tk.LEFT, padx=(0, 15))
        tk.Label(limits_row, text=CONFIG['TEXT']['label_writer_max_tokens'], font=font_body,
                bg=CONFIG['COLORS']['bg_white']).pack(side=tk.LEFT, padx=(0, 5))
        ttk.Spinbox(limits_row, from_=100, to=32000, increment=500, textvariable=self.writer_max_tokens_var,
                   width=7).pack(side=tk.LEFT)

        writer_btn_row = tk.Frame(writer_content, bg=CONFIG['COLORS']['bg_white'])
        writer_btn_row.pack(fill=tk.X, pady=(0, CONFIG['DIMENSIONS']['padding_normal']))
        tk.Checkbutton(writer_btn_row, text=CONFIG['TEXT']['chk_writer_auto'], variable=self.writer_auto_var,
                        command=self.save_settings, font=font_body, bg=CONFIG['COLORS']['bg_white'],
                        anchor=tk.W).pack(side=tk.LEFT, padx=(0, 10))
        tk.Button(writer_btn_row, text=CONFIG['TEXT']['btn_write_final'], command=self.write_final_piece,
                 **BUTTON_STYLES['primary']).pack(side=tk.LEFT)

        # Final Output — the finished piece the Final Writer produced
        tk.Label(writer_content, text=CONFIG['TEXT']['label_final_output'], font=font_label,
                bg=CONFIG['COLORS']['bg_white']).pack(anchor=tk.W, pady=(CONFIG['DIMENSIONS']['padding_normal'],
                                                                         CONFIG['DIMENSIONS']['padding_small']))

        final_output_frame = tk.Frame(writer_content, bg=CONFIG['COLORS']['bg_white'], relief=tk.FLAT, bd=0)
        final_output_frame.pack(fill=tk.BOTH, expand=False, pady=(0, CONFIG['DIMENSIONS']['padding_normal']))

        self.final_output = tk.Text(final_output_frame, height=CONFIG['DIMENSIONS']['final_output_height'],
                                    font=(CONFIG['FONTS']['family_mono'], CONFIG['FONTS']['size_small']),
                                    wrap=tk.WORD, bg=CONFIG['COLORS']['bg_secondary'],
                                    fg=CONFIG['COLORS']['text_primary'], relief=tk.FLAT, bd=0, state=tk.DISABLED)
        self.final_output.pack(fill=tk.BOTH, expand=True)

        final_btn_row = tk.Frame(writer_content, bg=CONFIG['COLORS']['bg_white'])
        final_btn_row.pack(fill=tk.X)
        tk.Button(final_btn_row, text=CONFIG['TEXT']['btn_copy_final'], command=self.copy_final_output,
                 **BUTTON_STYLES['neutral']).pack(side=tk.LEFT, padx=(0, 6))
        tk.Button(final_btn_row, text=CONFIG['TEXT']['btn_save_final'], command=self.save_final_output,
                 **BUTTON_STYLES['neutral']).pack(side=tk.LEFT)

        # Seed with two participant rows (or the saved set, if any were persisted) so there's
        # something usable on first open without making the user click "+ Add Participant" twice.
        saved_participants = self.settings.get('discussion_participants') or []
        if saved_participants:
            for p in saved_participants:
                self.add_discussion_participant_row(model_display=p.get('model'), role=p.get('role'))
        else:
            self.add_discussion_participant_row()
            self.add_discussion_participant_row()

    def switch_tab(self, tab_id):
        """Switch to a different tab"""
        for frame in self.tab_frames.values():
            frame.pack_forget()

        if tab_id in self.tab_frames:
            self.tab_frames[tab_id].pack(fill=tk.BOTH, expand=True)
            self.current_tab = tab_id
            self.update_tab_appearance()

    def update_tab_appearance(self):
        """Update tab button styling"""
        for tab_id, btn in self.tab_buttons.items():
            if tab_id == self.current_tab:
                btn.config(bg=CONFIG['COLORS']['tab_active'], fg=CONFIG['COLORS']['text_white'],
                           activebackground=BUTTON_STYLES['primary']['activebackground'],
                           activeforeground=CONFIG['COLORS']['text_white'])
            else:
                btn.config(bg=CONFIG['COLORS']['tab_inactive'], fg=CONFIG['COLORS']['text_primary'],
                           activebackground=BUTTON_STYLES['neutral']['activebackground'],
                           activeforeground=CONFIG['COLORS']['text_primary'])

    def _initialize_log_file(self):
        """Create or append to log file"""
        try:
            with open(self.log_file_path, 'a', encoding='utf-8') as f:
                f.write(f"\n{'='*60}\nSession started: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n{'='*60}\n")
        except OSError:
            pass

    def show_file_menu(self):
        """Show File menu"""
        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label=CONFIG['TEXT']['btn_settings'], command=self.open_settings)
        menu.add_separator()
        menu.add_command(label=CONFIG['TEXT']['btn_view_log'], command=self.open_log_file)
        menu.add_separator()
        menu.add_command(label=CONFIG['TEXT']['btn_exit'], command=self.exit_app)

        try:
            menu.post(self.root.winfo_x(), self.root.winfo_y() + 50)
        except tk.TclError:
            pass

    def exit_app(self):
        """Clean exit"""
        self.add_log("Application closing", "SYSTEM")
        self.save_settings()
        self.root.quit()


    def load_settings(self):
        """Load settings from file"""
        try:
            if os.path.exists(self.settings_file):
                import json
                with open(self.settings_file, 'r', encoding='utf-8') as f:
                    saved_settings = json.load(f)
                    self.settings.update(saved_settings)
                self.api_base = self.settings['api_base']
        except (OSError, json.JSONDecodeError, KeyError) as e:
            self.add_log(f"Could not load settings, using defaults: {str(e)}", "WARNING")

        # One-time migration: model pre-loading was removed and Auto load/unload became the default.
        # Older settings files saved auto_swap_models=False, which would keep it off forever, so
        # switch it on once; after that the checkbox is the user's own choice again.
        self.settings.pop('preload_models', None)
        if not self.settings.get('auto_swap_default_applied'):
            self.settings['auto_swap_models'] = True
            self.settings['auto_swap_default_applied'] = True

    def save_settings(self):
        """Save settings to file"""
        try:
            import json
            if hasattr(self, 'discussion_participant_rows'):
                self.settings['discussion_participants'] = self._collect_discussion_participants()
            if hasattr(self, 'discussion_rule_vars'):
                self.settings['discussion_rules'] = {k: v.get() for k, v in self.discussion_rule_vars.items()}
            if hasattr(self, 'final_writer_var'):
                self.settings['final_writer_model'] = self.final_writer_var.get()
            if hasattr(self, 'writer_auto_var'):
                self.settings['handoff_auto'] = bool(self.writer_auto_var.get())
            if hasattr(self, 'auto_swap_models_enabled'):
                self.settings['auto_swap_models'] = bool(self.auto_swap_models_enabled)
            with open(self.settings_file, 'w', encoding='utf-8') as f:
                json.dump(self.settings, f, indent=2)
            self.add_log("Settings saved", "INFO")
        except Exception as e:
            self.add_log(f"Error saving settings: {str(e)}", "ERROR")

    def open_settings(self):
        """Open settings window"""
        settings_win = tk.Toplevel(self.root)
        settings_win.title(CONFIG['TEXT']['settings_title'])
        w, h = CONFIG['DIMENSIONS']['settings_width'], CONFIG['DIMENSIONS']['settings_height']
        settings_win.geometry(f"{w}x{h}")
        settings_win.resizable(False, False)
        settings_win.grab_set()

        # Center on parent
        self.root.update_idletasks()
        x = self.root.winfo_x() + (self.root.winfo_width() // 2) - (w // 2)
        y = self.root.winfo_y() + (self.root.winfo_height() // 2) - (h // 2)
        settings_win.geometry(f"+{x}+{y}")

        # Note: Settings UI simplified for space - full version has all color picker fields
        # The key improvement is that all TEXT and COLORS/DIMENSIONS reference CONFIG

        messagebox.showinfo(CONFIG['TEXT']['popup_info'],
                           "Settings panel customizable via CONFIG dictionary at top of code.\n"
                           "Edit colors, text, dimensions, and API endpoints there.")
        settings_win.destroy()

    def open_log_file(self):
        """Open log file"""
        if not os.path.exists(self.log_file_path):
            messagebox.showwarning(CONFIG['TEXT']['popup_warning'],
                                  f"{CONFIG['TEXT']['msg_no_log']}\n{self.log_file_path}")
            return

        try:
            if os.name == 'nt':
                os.startfile(self.log_file_path)
            else:
                os.system(f'open "{self.log_file_path}"')
            self.add_log("Opened log file", "INFO")
        except Exception as e:
            messagebox.showerror(CONFIG['TEXT']['popup_error'], f"Could not open log:\n{str(e)}")
            self.add_log(f"Error opening log: {str(e)}", "ERROR")

    def add_log(self, message, log_type="INFO"):
        """Add log message"""
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        log_entry = f"[{timestamp}] {log_type}: {message}"
        self.error_log.append(log_entry)

        try:
            with open(self.log_file_path, 'a', encoding='utf-8') as f:
                f.write(log_entry + "\n")
        except OSError:
            pass

        if len(self.error_log) > 1000:
            self.error_log.pop(0)

    def run_startup_checks(self):
        """Run startup checks"""
        splash = LoadingSplash(self.root)
        self.add_log("Starting application...", "SYSTEM")

        def startup_thread():
            self.is_connected = self.check_connection()
            self.refresh_models()
            # splash.close() and deiconify() touch Tkinter widgets — this
            # function runs on a background thread, so marshal them onto
            # the main thread instead of calling them directly.
            def finish_startup():
                splash.close()
                self.root.deiconify()
            self.root.after(0, finish_startup)

        def launch_startup_thread():
            # Do NOT start this thread directly from __init__ (i.e. before
            # root.mainloop() has actually begun running). check_connection()
            # and refresh_models() call self.root.after() from this background
            # thread, and if that fires before Tk's event loop is dispatching
            # — which it can easily be on a slower machine, or if LM Studio is
            # unreachable and the connection attempt returns fast — Tkinter
            # raises "RuntimeError: main thread is not in main loop". That
            # exception is unhandled here, so it kills this thread before it
            # ever reaches finish_startup(): the splash screen never closes
            # and the main window never appears, with only a background
            # thread traceback (no popup, no console error) as any hint why.
            # Scheduling the thread's start via root.after() from the MAIN
            # thread guarantees this callback can't fire until mainloop() is
            # already running and pumping events, so the race can't happen.
            thread = threading.Thread(target=startup_thread, daemon=True)
            thread.start()

        self.root.after(50, launch_startup_thread)

    def update_resolution(self, event=None):
        """Update resolution display"""
        width = self.root.winfo_width()
        height = self.root.winfo_height()
        if width > 1 and height > 1:
            self.resolution_label.config(text=f"{width}x{height}")

    def toggle_window_lock(self):
        """Toggle window lock"""
        self.window_locked = not self.window_locked
        self.root.resizable(not self.window_locked, not self.window_locked)
        self.lock_button.config(text="🔒" if self.window_locked else "🔓")

    def refresh_models(self):
        """Fetch available models. Safe to call from any thread — this method
        itself may run on a background thread (e.g. startup), so all widget
        updates are marshalled onto the main thread via root.after()."""
        try:
            response = requests.get(f"{self.api_base}{CONFIG['API']['models_endpoint']}",
                                   timeout=CONFIG['API']['timeout_models'])
            if response.status_code == 200:
                data = response.json()
                models = data.get("models", [])

                self.model_keys = {}

                llm_models = [m for m in models if m.get("type") == "llm"]

                if llm_models:
                    for idx, model in enumerate(llm_models):
                        key = model.get("key", "unknown")
                        self.model_keys[idx] = key
                    self.root.after(0, lambda: self.lms_count_label.config(text=str(len(llm_models))))
                    self.add_log(f"Loaded {len(llm_models)} models", "INFO")
                else:
                    self.root.after(0, lambda: self.lms_count_label.config(text="0"))
                    self.add_log("No LLM models found", "WARNING")

                self.update_model_dropdowns()
            else:
                self.root.after(0, lambda: self.lms_count_label.config(text="0"))
                self.add_log(f"Failed to load models: {response.status_code}", "ERROR")
        except Exception as e:
            self.root.after(0, lambda: self.lms_count_label.config(text="0"))
            self.add_log(f"Error fetching models: {str(e)}", "ERROR")

    def on_model_select(self, event):
        """Handle model selection"""
        if self.loading_model:
            return

        idx = self.models_listbox.nearest(event.y)
        if idx >= 0:
            model_key = self.model_keys.get(idx)
            if model_key:
                self.load_model(model_key)

    def load_model(self, model_key):
        """Load a model"""
        if not model_key:
            messagebox.showerror(CONFIG['TEXT']['popup_error'], CONFIG['TEXT']['msg_no_model_identified'])
            self.add_log("Failed to identify model", "ERROR")
            return

        self.loading_model = True

        try:
            response = requests.post(
                f"{self.api_base}{CONFIG['API']['load_model_endpoint']}",
                json={"model": model_key},
                timeout=CONFIG['API']['timeout_load']
            )

            if response.status_code == 200:
                self.current_model = model_key
                self.check_connection()
                messagebox.showinfo(CONFIG['TEXT']['popup_success'], f"Model loaded: {model_key}")
                self.add_log(f"Model loaded: {model_key}", "INFO")
            else:
                messagebox.showerror(CONFIG['TEXT']['popup_error'],
                                    f"{CONFIG['TEXT']['error_failed_load']}{response.status_code}")
                self.add_log(f"Failed to load model: {response.status_code}", "ERROR")
        except Exception as e:
            messagebox.showerror(CONFIG['TEXT']['popup_error'], f"{CONFIG['TEXT']['error_load_model']}\n{str(e)}")
            self.add_log(f"Error loading model: {str(e)}", "ERROR")
        finally:
            self.loading_model = False

    def refresh_script(self):
        """Refresh connection"""
        self.check_connection()
        self.refresh_models()
        self.add_log("Script refreshed", "INFO")

    def restart_script(self):
        """Restart application"""
        if messagebox.askyesno(CONFIG['TEXT']['popup_info'], CONFIG['TEXT']['msg_restart_confirm']):
            self.root.quit()
            subprocess.Popen([sys.executable, sys.argv[0]])
            sys.exit()

    def check_connection(self):
        """Check LM Studio connection. Safe to call from any thread — this
        method itself may run on a background thread (e.g. startup), so all
        widget updates are marshalled onto the main thread via root.after()."""
        try:
            response = requests.get(f"{self.api_base}{CONFIG['API']['models_endpoint']}",
                                   timeout=CONFIG['API']['timeout_connect'])
            self.root.after(0, lambda: self.status_label.config(
                text=CONFIG['TEXT']['status_connected'], foreground=CONFIG['COLORS']['status_online']))
            self.add_log("Connected to LM Studio", "INFO")

            if response.status_code == 200:
                data = response.json()
                models = data.get("models", [])
                llm_models = [m for m in models if m.get("type") == "llm"]
                self.root.after(0, lambda: self.lms_count_label.config(text=str(len(llm_models))))
            return True
        except Exception as e:
            self.root.after(0, lambda: self.status_label.config(
                text=CONFIG['TEXT']['status_disconnected'], foreground=CONFIG['COLORS']['status_offline']))
            self.root.after(0, lambda: self.lms_count_label.config(text="0"))
            self.add_log(f"Lost connection to LM Studio: {str(e)}", "ERROR")
            return False

    def update_system_resources(self):
        """Periodically update system resources"""
        def resource_thread():
            try:
                ram_info = psutil.virtual_memory()
                ram_used = ram_info.used / (1024**3)
                ram_total = ram_info.total / (1024**3)
                ram_percent = ram_info.percent

                vram_used = None
                vram_total = None
                try:
                    import subprocess
                    result = subprocess.run(
                        ['nvidia-smi', '--query-gpu=memory.used,memory.total', '--format=csv,nounits,noheader'],
                        capture_output=True, text=True, timeout=2
                    )
                    if result.returncode == 0:
                        parts = result.stdout.strip().split(',')
                        if len(parts) == 2:
                            vram_used = float(parts[0].strip()) / 1024
                            vram_total = float(parts[1].strip()) / 1024
                except Exception:
                    pass  # nvidia-smi not available — GPU memory stats just won't be shown

                self.root.after(0, self._update_resource_ui, ram_used, ram_total, ram_percent, vram_used, vram_total)
            except Exception as e:
                self.add_log(f"Error getting system resources: {str(e)}", "ERROR")

        thread = threading.Thread(target=resource_thread, daemon=True)
        thread.start()
        self.root.after(2000, self.update_system_resources)

    def _update_resource_ui(self, ram_used, ram_total, ram_percent, vram_used, vram_total):
        """Update resource display"""
        try:
            if ram_used is not None:
                ram_text = f"{ram_used:.1f} GB / {ram_total:.1f} GB ({ram_percent:.0f}%)"
                self.ram_label.config(text=ram_text)

            if vram_used is not None and vram_total is not None:
                vram_percent = (vram_used / vram_total) * 100
                vram_text = f"{vram_used:.1f} GB / {vram_total:.1f} GB ({vram_percent:.0f}%)"
                self.vram_label.config(text=vram_text)

                total_used = vram_used + ram_used
                total_available = vram_total + ram_total
                total_percent = (total_used / total_available) * 100
                total_text = f"{total_used:.1f} GB / {total_available:.1f} GB ({total_percent:.0f}%)"
                self.total_label.config(text=total_text)
            else:
                self.vram_label.config(text="N/A (nvidia-smi not found)")
                if ram_used is not None:
                    total_text = f"{ram_used:.1f} GB / {ram_total:.1f} GB ({ram_percent:.0f}%)"
                    self.total_label.config(text=total_text)
        except Exception as e:
            self.add_log(f"Error updating resource display: {str(e)}", "ERROR")


    # ====================================================================
    # SHARED HELPERS + DISCUSSION RULES + FINAL WRITER
    # ====================================================================

    def _load_model_silent(self, model_key, log=None):
        """Explicitly ask LM Studio to load a model, without the popup dialogs load_model() shows.
        Used by Auto load/unload (_ensure_model_active) just before a model is needed."""
        try:
            response = requests.post(
                f"{self.api_base}{CONFIG['API']['load_model_endpoint']}",
                json={"model": model_key},
                timeout=CONFIG['API']['timeout_swap_load']   # big models can take a while to load
            )
            if response.status_code == 200:
                self.add_log(f"Model loaded: {model_key}", "INFO")
                if log:
                    log(f"  ✓ Loaded: {model_key}\n")
                return True
            self.add_log(f"Failed to load model {model_key}: {response.status_code}", "WARNING")
            if log:
                log(f"  ⚠ Could not load {model_key} (HTTP {response.status_code}) — "
                    f"it will still load on first use\n")
            return False
        except Exception as e:
            self.add_log(f"Error loading model {model_key}: {str(e)}", "WARNING")
            if log:
                log(f"  ⚠ Could not load {model_key}: {str(e)} — it will still load on first use\n")
            return False

    def _get_loaded_llm_instances(self):
        """{model_key: [instance_id, ...]} for every LLM LM Studio currently holds in memory,
        or None if the list couldn't be read."""
        try:
            response = requests.get(f"{self.api_base}{CONFIG['API']['models_endpoint']}",
                                    timeout=CONFIG['API']['timeout_models'])
            if response.status_code != 200:
                self.add_log(f"Could not read loaded models: HTTP {response.status_code}", "WARNING")
                return None
            loaded = {}
            for m in response.json().get("models", []):
                if m.get("type") != "llm":
                    continue
                ids = []
                for inst in (m.get("loaded_instances") or []):
                    inst_id = inst.get("id") if isinstance(inst, dict) else inst
                    if inst_id:
                        ids.append(inst_id)
                if ids:
                    loaded[m.get("key")] = ids
            return loaded
        except Exception as e:
            self.add_log(f"Could not read loaded models: {str(e)}", "WARNING")
            return None

    def _unload_instance(self, instance_id):
        """Ask LM Studio to unload one loaded model instance (needs LM Studio 0.4.0+)."""
        try:
            response = requests.post(
                f"{self.api_base}{CONFIG['API']['unload_model_endpoint']}",
                json={"instance_id": instance_id},
                timeout=CONFIG['API']['timeout_load']
            )
            if response.status_code == 200:
                self.add_log(f"Model unloaded: {instance_id}", "INFO")
                return True
            self.add_log(f"Failed to unload {instance_id}: HTTP {response.status_code}", "WARNING")
            return False
        except Exception as e:
            self.add_log(f"Error unloading {instance_id}: {str(e)}", "WARNING")
            return False

    def _ensure_model_active(self, model_key, log=None):
        """Auto load/unload: make model_key the only LLM in VRAM — unload every other loaded LLM,
        then load model_key if it isn't already loaded. Does nothing unless the 'Auto load/unload
        models' toggle is on, so it is safe to call before every model request. If the loaded list
        can't be read, it backs off and leaves loading to LM Studio."""
        if not self.auto_swap_models_enabled or not model_key:
            return True
        with self._model_swap_lock:
            loaded = self._get_loaded_llm_instances()
            if loaded is None:
                if log:
                    log("  ⚠ Auto load/unload couldn't read LM Studio's loaded models — leaving loading to LM Studio\n")
                return False
            for key, instance_ids in loaded.items():
                if key == model_key:
                    continue
                for instance_id in instance_ids:
                    if self._unload_instance(instance_id):
                        if log:
                            log(f"  ⏏ Unloaded: {key}\n")
                    elif log:
                        log(f"  ⚠ Could not unload {key} — it may still be using VRAM\n")
            if model_key in loaded:
                return True
            if log:
                log(f"  ⏳ Loading {model_key}...\n")
            return self._load_model_silent(model_key, log)

    # ====================================================================
    # DISCUSSION RULES — the old Research Rules, retargeted at discussion turns
    # ====================================================================

    def _discussion_rule_enabled(self, rule_key):
        var = self.discussion_rule_vars.get(rule_key)
        return bool(var.get()) if var is not None else False

    def _split_statements(self, text):
        """Split a turn into (statement, trailing_whitespace) pairs on sentence ends, so kept
        statements can be re-joined with their original spacing and paragraph breaks."""
        parts = re.split(r'(?<=[.!?])(\s+)', text.strip())
        statements = parts[0::2]
        separators = parts[1::2] + [""]
        return [(s, sep) for s, sep in zip(statements, separators) if s.strip()]

    def _rebuild_statements(self, pairs, keep):
        """Join the kept statements back together. If a dropped statement ended a paragraph, its
        line break is passed to the statement before it so the turn keeps its paragraph shape."""
        out = []
        for (statement, sep), k in zip(pairs, keep):
            if k:
                out.append([statement, sep])
            elif out and "\n" in sep and "\n" not in out[-1][1]:
                out[-1][1] = sep
        return "".join(s + sep for s, sep in out).strip()

    def _discussion_rule_ask(self, prompt, model_key, log, max_tokens=3000, temperature=0.2):
        """One extra low-temperature API call for a Discussion Rule. Returns the reply text, or
        None if the call failed — callers treat None as "skip this check", never as a verdict."""
        self._ensure_model_active(model_key, log)
        try:
            resp = requests.post(
                f"{self.api_base}{CONFIG['API']['chat_endpoint']}",
                json={"model": model_key, "messages": [{"role": "user", "content": prompt}],
                      "temperature": temperature, "max_tokens": max_tokens},
                timeout=self.timeout_var.get()
            )
            if resp.status_code == 200:
                result = resp.json()
                if 'usage' in result:
                    self.session_tokens += result['usage'].get('total_tokens', 0)
                    self.root.after(0, lambda: self.session_tokens_label.config(text=str(self.session_tokens)))
                return result['choices'][0]['message']['content']
        except requests.exceptions.RequestException as e:
            log(f"  ⚠ Rule check call failed ({str(e)}) — skipping this check for this turn\n")
        return None

    def _parse_verdict_list(self, text, negative_word, count):
        """Parse a numbered list of per-statement verdicts like '1. RETRACT' / '2. CONFIRM'.
        Returns a list of `count` booleans (True = keep the statement at that index). Any line
        that can't be parsed, or a response that never arrives, defaults to True — a parsing
        failure or a dropped API call should never silently destroy an already-written turn."""
        keep = [True] * count
        if not text:
            return keep
        for line in text.strip().split('\n'):
            m = re.match(r'\s*(\d+)[\.\):]\s*(.+)', line)
            if not m:
                continue
            idx = int(m.group(1)) - 1
            if 0 <= idx < count and negative_word.upper() in m.group(2).strip().upper():
                keep[idx] = False
        return keep

    def _apply_discussion_rules(self, turn_text, topic, speaker, log):
        """Runs whichever statement-level Discussion Rules are currently ticked (Double-Check,
        Verify Claims, Cross-Check) against a freshly written turn. The turn is split into
        numbered statements; Double-Check and Verify Claims drop the statements they reject,
        while Cross-Check keeps everything but appends a visible note about any conflict (in a
        debate, disagreement is normal — the next speakers should see it, not have it deleted).
        Each rule is its own extra API call and runs sequentially, so later rules only see the
        statements earlier rules kept. Returns the resulting turn text, or "" if every
        statement was dropped."""
        statement_rules = [k for k in ('double_check', 'verify_claims', 'cross_check')
                           if self._discussion_rule_enabled(k)]
        if not statement_rules:
            return turn_text

        pairs = self._split_statements(turn_text)
        if not pairs:
            return turn_text

        checkable = min(CONFIG['DISCUSSION']['rule_max_statements'], len(pairs))
        keep = [True] * len(pairs)
        model_key = speaker["model_key"]
        rules_cfg = CONFIG['DISCUSSION_RULES']

        def run_check(build_prompt, negative_word):
            """Ask one rule's question about the statements still kept. Returns
            (keep_flags, active_indices), aligned with each other."""
            active = [i for i in range(checkable) if keep[i]]
            if not active:
                return [], []
            claims_list = "\n".join(f"{n + 1}. {pairs[i][0].strip()}" for n, i in enumerate(active))
            reply = self._discussion_rule_ask(build_prompt(claims_list), model_key, log)
            return self._parse_verdict_list(reply, negative_word, len(active)), active

        if 'double_check' in statement_rules:
            flags, active = run_check(
                lambda claims: rules_cfg['double_check']['verify_template'].format(
                    topic=topic, claims_list=claims), "RETRACT")
            dropped = 0
            for i, k in zip(active, flags):
                if not k:
                    keep[i] = False
                    dropped += 1
            if dropped:
                log(f"  ⊘ Double-check: {dropped} statement(s) retracted by the speaker on review\n")

        if 'verify_claims' in statement_rules:
            flags, active = run_check(
                lambda claims: rules_cfg['verify_claims']['selfcheck_template'].format(
                    topic=topic, claims_list=claims), "QUESTIONABLE")
            dropped = 0
            for i, k in zip(active, flags):
                if not k:
                    keep[i] = False
                    dropped += 1
            if dropped:
                log(f"  ⊘ Verify Claims: {dropped} statement(s) flagged questionable against the "
                    f"model's own knowledge — removed\n")

        conflicts = []
        if 'cross_check' in statement_rules and self.discussion_transcript:
            existing = self._summarize_discussion()
            flags, active = run_check(
                lambda claims: rules_cfg['cross_check']['crosscheck_template'].format(
                    topic=topic, existing_summary=existing, claims_list=claims), "CONTRADICTS")
            conflicts = [pairs[i][0].strip() for i, k in zip(active, flags) if not k]

        if not any(keep):
            return ""

        if all(keep):
            new_text = turn_text
        else:
            new_text = self._rebuild_statements(pairs, keep)

        if conflicts:
            quoted = "; ".join('"' + (c[:90] + ('…' if len(c) > 90 else '')) + '"' for c in conflicts)
            log(f"  ⚠ Cross-check: {len(conflicts)} statement(s) conflict with earlier discussion\n")
            new_text += f"\n\n[Cross-check: conflicts with earlier discussion — {quoted}. Next speakers: please reconcile.]"

        return new_text

    def _run_investigate_followup(self, turn_text, topic, speaker, log):
        """'Investigate Deeper' rule: asks the speaker one follow-up about the strongest factual
        claim in the turn it just gave. Returns the extra detail to append to the turn, or ""
        when it has nothing more specific to add (or the call failed)."""
        rcfg = CONFIG['DISCUSSION_RULES']['investigate']
        prompt = rcfg['followup_template'].format(topic=topic, turn=turn_text[:3000])
        reply = self._discussion_rule_ask(prompt, speaker["model_key"], log, max_tokens=4000, temperature=0.4)
        if not reply:
            return ""
        reply = reply.strip()
        if not reply or reply.upper().startswith("NO FURTHER DETAIL"):
            return ""
        return reply


    # ====================================================================
    # FINAL WRITER
    # ====================================================================

    def _a_an(self, phrase):
        return "an" if phrase[:1].lower() in "aeiou" else "a"

    def _output_profile(self, category):
        profiles = CONFIG['OUTPUT_PROFILES']
        return profiles.get(category) or profiles['Story']

    def _engine_active_for(self, category):
        """True if the Constraint Engine is loaded, enabled, and this Category isn't one it should skip."""
        return bool(self.engine_loaded and self.engine and self.engine_enabled_var.get()
                    and category not in CONFIG['ENGINE_SKIP_CATEGORIES'])

    def _resolve_discussion_participants(self):
        """Current participant rows resolved to model keys (rows with no model are skipped)."""
        participants = []
        for entry in self._collect_discussion_participants():
            model_display = entry["model"]
            role = entry["role"]
            if model_display == "None Selected" or not role:
                continue
            model_key = self.model_display_names.get(model_display)
            if model_key:
                participants.append({"model_display": model_display, "model_key": model_key, "role": role})
        return participants

    def _run_moderator_synthesis_if_needed(self, topic, participants, log):
        """Write the Moderator's closing synthesis into the transcript if a Moderator is taking
        part and the discussion has moved on since the last synthesis. Used both when a
        discussion stalls and when it is stopped, so 'finished' always ends with a synthesis."""
        moderator = next((p for p in participants if p["role"] == "Moderator"), None)
        if not moderator:
            return
        if len(self.discussion_transcript) <= self._synth_at_len:
            return  # nothing new since the last synthesis
        log("\n▶ Moderator synthesis...\n")
        synthesis = self._get_moderator_synthesis(topic, moderator["model_key"], log)
        if synthesis:
            log(f"\n{'═' * 50}\n  MODERATOR SYNTHESIS\n{'═' * 50}\n\n{synthesis}\n")
            self.discussion_transcript.append({
                "speaker": moderator["model_display"], "role": "Moderator (synthesis)",
                "text": synthesis, "round": self.discussion_round
            })
            self._save_discussion_transcript()
            self._synth_at_len = len(self.discussion_transcript)

    def _build_writer_knowledge(self):
        """Everything the Final Writer gets to know from the discussion. If the full raw
        transcript fits in the configured budget it is sent whole (nothing lost); otherwise
        the Moderator's closing synthesis, the compacted summary and the latest turns are used."""
        budget = CONFIG['FINAL_WRITER']['knowledge_max_chars']
        full = "\n\n".join(f"[{t['role']} — {t['speaker']}]: {t['text']}" for t in self.discussion_transcript)
        if len(full) <= budget:
            return full

        parts = []
        synthesis = next((t['text'] for t in reversed(self.discussion_transcript)
                          if t['role'] == "Moderator (synthesis)"), "")
        if synthesis:
            parts.append(f"[Moderator's closing synthesis]\n{synthesis}")
        if self.discussion_summary:
            parts.append(f"[Summary of the discussion]\n{self.discussion_summary}")
        recent = self._recent_turns_text()
        if recent:
            parts.append(f"[Most recent turns]\n{recent}")
        return "\n\n".join(parts)[:budget]

    def _run_final_writer(self, topic, log, compactor_key=None, force=False):
        """Have the Final Writer model write the finished piece from the whole discussion, in the
        Category chosen on the Discussion card, and show it in the Final Output box. Runs on a
        background thread (the discussion loop's, or one started by the Write Final Piece button)."""
        try:
            if not self.discussion_transcript:
                log("  ⚠ Nothing to write from — the discussion has no turns yet\n")
                return
            if not force and self._writer_at_len == len(self.discussion_transcript):
                return  # nothing new since the last time the Final Writer ran

            writer_display = self.final_writer_var.get()
            if writer_display == "None Selected":
                log("\n⚠ No Final Writer model selected — pick one under \"Final Writer Model\" to have "
                    "the finished piece written.\n")
                return
            writer_key = self.model_display_names.get(writer_display)
            if not writer_key:
                log(f"\n⚠ Final Writer model '{writer_display}' not found in available models\n")
                return

            category = self.output_category_var.get()
            profile = self._output_profile(category)
            form = profile['form']
            article = self._a_an(form)
            instructions = self.writer_instructions_var.get().strip()
            instructions_block = f"EXTRA INSTRUCTIONS FROM THE USER: {instructions}\n\n" if instructions else ""

            if compactor_key:
                self._maybe_compact_discussion(topic, compactor_key, log)
            knowledge = self._build_writer_knowledge()

            log(f"\n✍ Final Writer ({writer_display}) is writing the {form} from the whole discussion...\n")
            self.add_log(f"Final Writer started: {writer_display}, category={category}", "INFO")
            if self.auto_swap_models_enabled:
                self._ensure_model_active(writer_key, log)

            prompt = CONFIG['FINAL_WRITER']['final_writer_template'].format(
                article=article, form=form, topic=topic, instructions_block=instructions_block,
                knowledge=knowledge, create_focus=profile['create_focus'])
            response = requests.post(
                f"{self.api_base}{CONFIG['API']['chat_endpoint']}",
                json={
                    "model": writer_key,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": self.writer_temp_var.get(),
                    "max_tokens": self.writer_max_tokens_var.get()
                },
                timeout=self.timeout_var.get()
            )
            if response.status_code != 200:
                log(f"  ❌ Final Writer API error {response.status_code}\n")
                self.add_log(f"Final Writer API error: {response.status_code}", "ERROR")
                return

            result = response.json()
            text = result['choices'][0]['message']['content'].strip()
            if 'usage' in result:
                tokens_used = result['usage'].get('total_tokens', 0)
                self.session_tokens += tokens_used
                self.reasoning_tokens = tokens_used
                self.root.after(0, lambda: self.session_tokens_label.config(text=str(self.session_tokens)))
                self.root.after(0, lambda: self.reasoning_tokens_label.config(text=str(tokens_used)))
            if not text:
                log("  ⚠ The Final Writer produced no text\n")
                return

            # Same auto-fix pass the discussion turns get — skipped for categories (Coding) it would damage
            if self._engine_active_for(category):
                validation_result = self.engine.validate(text, auto_fix=True)
                if validation_result.get('success') and 'data' in validation_result:
                    text = validation_result['data']
            elif self.engine_loaded and self.engine_enabled_var.get():
                log(f"  (Constraint Engine skipped for the {category} category — it is tuned for prose)\n")

            self._writer_at_len = len(self.discussion_transcript)
            log(f"  ✓ Final piece written ({category}, {len(text)} chars) — see Final Output below\n")
            self.add_log(f"Final Writer finished: category={category}, {len(text)} chars", "INFO")
            self.root.after(0, self._show_final_output, text, category)

        except requests.exceptions.RequestException as e:
            log(f"\n❌ Final Writer connection error: {str(e)}\n")
            self.add_log(f"Final Writer connection error: {str(e)}", "ERROR")
        except Exception as e:
            log(f"\n❌ Final Writer error: {str(e)}\n")
            self.add_log(f"Final Writer error: {str(e)}", "ERROR")

    def _show_final_output(self, text, category):
        """Main-thread half of the Final Writer: put the finished piece in the Final Output box."""
        self.final_output_category = category
        self.final_output.config(state=tk.NORMAL)
        self.final_output.delete("1.0", tk.END)
        self.final_output.insert(tk.END, text)
        self.final_output.see("1.0")
        self.final_output.config(state=tk.DISABLED)

    def write_final_piece(self):
        """'Write Final Piece' button: run the Final Writer on the current discussion on demand
        (the same path the automatic end-of-discussion step uses)."""
        if not self.discussion_transcript:
            messagebox.showwarning(CONFIG['TEXT']['popup_warning'], CONFIG['TEXT']['msg_no_transcript_yet'])
            return
        if self.discussion_running and not self.discussion_paused:
            messagebox.showwarning(CONFIG['TEXT']['popup_warning'], CONFIG['TEXT']['msg_writer_pause_first'])
            return
        if self.final_writer_var.get() == "None Selected":
            messagebox.showwarning(CONFIG['TEXT']['popup_warning'], CONFIG['TEXT']['msg_no_final_writer'])
            return
        if self._writer_running:
            messagebox.showwarning(CONFIG['TEXT']['popup_warning'], CONFIG['TEXT']['msg_writer_in_progress'])
            return
        topic = self.discussion_topic or self.discussion_topic_var.get().strip()
        if not topic:
            messagebox.showwarning(CONFIG['TEXT']['popup_warning'], CONFIG['TEXT']['msg_no_discussion_topic'])
            return

        participants = self._resolve_discussion_participants()
        compactor_key = participants[0]["model_key"] if participants else None
        self._writer_running = True

        def _run():
            def log(text):
                self.root.after(0, self._append_discussion_output, text)
            try:
                if participants:
                    self._run_moderator_synthesis_if_needed(topic, participants, log)
                self._run_final_writer(topic, log, compactor_key=compactor_key, force=True)
            finally:
                self._writer_running = False

        threading.Thread(target=_run, daemon=True).start()

    def copy_final_output(self):
        """Copy the finished piece to the clipboard"""
        text = self.final_output.get("1.0", tk.END).strip()
        if not text:
            messagebox.showwarning(CONFIG['TEXT']['popup_warning'], CONFIG['TEXT']['msg_no_final_output'])
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        messagebox.showinfo(CONFIG['TEXT']['popup_info'], CONFIG['TEXT']['msg_final_copied'])
        self.add_log("Copied final output", "INFO")

    def save_final_output(self):
        """Save the finished piece to a text file in Downloads"""
        text = self.final_output.get("1.0", tk.END).strip()
        if not text:
            messagebox.showwarning(CONFIG['TEXT']['popup_warning'], CONFIG['TEXT']['msg_no_final_output'])
            return

        category = re.sub(r'[^a-zA-Z0-9_-]+', '_', getattr(self, 'final_output_category', '') or 'piece').strip('_').lower()
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"final_{category}_{timestamp}.txt"
        filepath = os.path.join(os.path.expanduser("~"), "Downloads", filename)

        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(text)
            messagebox.showinfo(CONFIG['TEXT']['popup_info'], f"Saved:\n{filename}")
            self.add_log(f"Saved final output: {filename}", "INFO")
        except Exception as e:
            messagebox.showerror(CONFIG['TEXT']['popup_error'], f"{CONFIG['TEXT']['error_save_file']}\n{str(e)}")
            self.add_log(f"Error saving final output: {str(e)}", "ERROR")

    def update_model_dropdowns(self):
        """Refresh every model dropdown from LM Studio's model list. Safe to call from any thread —
        this method itself may run on a background thread (e.g. startup), so all widget updates
        are marshalled onto the main thread via root.after()."""
        try:
            response = requests.get(f"{self.api_base}{CONFIG['API']['models_endpoint']}",
                                   timeout=CONFIG['API']['timeout_models'])
            if response.status_code == 200:
                data = response.json()
                models = data.get("models", [])
                llm_models = [m for m in models if m.get("type") == "llm"]

                model_options = ["None Selected"]
                self.model_display_names = {}

                for model in llm_models:
                    display_name = model.get("display_name", "Unknown")
                    key = model.get("key", "unknown")
                    quantization = model.get("quantization", {}).get("name", "")
                    display_text = f"{display_name} ({quantization})" if quantization else display_name

                    model_options.append(display_text)
                    self.model_display_names[display_text] = key

                def update_dropdowns():
                    # Final Writer dropdown (Discussion card)
                    if hasattr(self, 'final_writer_dropdown'):
                        self.final_writer_dropdown['values'] = model_options

                    # Discussion participant dropdowns
                    if hasattr(self, 'discussion_participant_rows'):
                        for row in self.discussion_participant_rows:
                            row['model_dropdown']['values'] = model_options
                self.root.after(0, update_dropdowns)
        except Exception as e:
            self.add_log(f"Error updating model dropdowns: {str(e)}", "ERROR")


    # ====================================================================
    # DISCUSSION MODE — multi-model roundtable with shared, compacted transcript
    # ====================================================================

    def add_discussion_participant_row(self, model_display=None, role=None):
        """Add one participant row (model dropdown + role dropdown + remove button) to the
        Discussion card. Called for the two seed rows on first build, once per saved participant
        when restoring from settings, and on every '+ Add Participant' click."""
        model_options = ["None Selected"] + list(self.model_display_names.keys())
        role_options = list(CONFIG['DISCUSSION_ROLES'].keys())

        row_frame = tk.Frame(self.discussion_participants_frame, bg=CONFIG['COLORS']['bg_white'])
        row_frame.pack(fill=tk.X, pady=(0, 6))

        model_var = tk.StringVar(value=model_display if model_display in model_options else "None Selected")
        model_dropdown = ttk.Combobox(row_frame, textvariable=model_var, state="readonly",
                                       values=model_options, width=CONFIG['DEFAULTS']['model_display_width'])
        model_dropdown.pack(side=tk.LEFT, padx=(0, 6))

        role_var = tk.StringVar(value=role if role in role_options else (role_options[0] if role_options else ""))
        ttk.Combobox(row_frame, textvariable=role_var, state="readonly",
                    values=role_options, width=27,
                    height=max(10, len(role_options))).pack(side=tk.LEFT, padx=(0, 6))

        row = {"frame": row_frame, "model_var": model_var, "role_var": role_var, "model_dropdown": model_dropdown}

        remove_btn = tk.Button(
            row_frame, text=CONFIG['TEXT']['btn_remove_participant'],
            command=lambda: self.remove_discussion_participant_row(row),
            **BUTTON_STYLES['danger']
        )
        remove_btn.pack(side=tk.LEFT)

        self.discussion_participant_rows.append(row)
        return row

    def remove_discussion_participant_row(self, row):
        """Remove one participant row. Refuses to drop below 1 row left in the UI — start_discussion
        separately enforces at least 2 with valid models/roles before a run can begin."""
        if row not in self.discussion_participant_rows:
            return
        if len(self.discussion_participant_rows) <= 1:
            return
        row["frame"].destroy()
        self.discussion_participant_rows.remove(row)

    def _collect_discussion_participants(self):
        """Current participant rows as plain dicts — used both for settings persistence and to
        resolve who's actually speaking when a run starts."""
        return [{"model": row["model_var"].get(), "role": row["role_var"].get()}
                for row in self.discussion_participant_rows]

    def _get_discussion_compact_interval(self):
        """Rounds between periodic auto-merges, or 0 if Auto-Merge is set to Off. Read fresh each
        time so changing the dropdown mid-run takes effect on the next check without restarting."""
        return DISCUSSION_COMPACT_INTERVALS.get(self.discussion_compact_interval_var.get(), 10)

    def start_discussion(self):
        """Start (or resume into) the discussion loop on a background thread"""
        if self.discussion_running:
            messagebox.showwarning(CONFIG['TEXT']['popup_warning'], CONFIG['TEXT']['msg_discussion_running'])
            return

        topic = self.discussion_topic_var.get().strip()
        if not topic:
            messagebox.showwarning(CONFIG['TEXT']['popup_warning'], CONFIG['TEXT']['msg_no_discussion_topic'])
            return

        participants = []
        for entry in self._collect_discussion_participants():
            model_display = entry["model"]
            role = entry["role"]
            if model_display == "None Selected" or not role:
                continue
            model_key = self.model_display_names.get(model_display)
            if not model_key:
                self.add_log(f"Discussion participant model '{model_display}' not found in available models — skipping", "ERROR")
                continue
            participants.append({"model_display": model_display, "model_key": model_key, "role": role})

        if len(participants) < 2:
            messagebox.showwarning(CONFIG['TEXT']['popup_warning'], CONFIG['TEXT']['msg_discussion_too_few'])
            return

        # Persist the current Discussion card choices right away (also saved again on exit).
        self.settings['discussion_compact_interval_label'] = self.discussion_compact_interval_var.get()
        self.settings['discussion_participants'] = self._collect_discussion_participants()

        if self.discussion_topic and self.discussion_topic != topic and self.discussion_transcript:
            if not messagebox.askyesno(
                CONFIG['TEXT']['msg_new_topic_title'],
                f"Current transcript is for '{self.discussion_topic}'. Starting '{topic}' will replace it "
                f"in memory (already-saved files on disk are unaffected). Continue?"
            ):
                return
            self.discussion_transcript = []
            self.discussion_summary = ""
            self.discussion_round = 0
            self.discussion_stall_streak = 0
            self.discussion_last_compaction_round = 0
            self.discussion_transcript_path = None
            self._synth_at_len = 0
            self._writer_at_len = -1

        self.discussion_topic = topic
        self.discussion_running = True
        self.discussion_paused = False
        self.discussion_stop_requested = False
        self.pause_discussion_btn.config(text=CONFIG['TEXT']['btn_discussion_pause'])
        self._update_discussion_status_display()
        self.add_log(f"Discussion started: topic='{topic}', {len(participants)} participants", "INFO")

        thread = threading.Thread(target=self._discussion_loop, args=(topic, participants), daemon=True)
        thread.start()

    def pause_discussion(self):
        """Toggle pause/resume — the loop thread checks this flag between turns"""
        if not self.discussion_running:
            return
        self.discussion_paused = not self.discussion_paused
        self.pause_discussion_btn.config(
            text=CONFIG['TEXT']['btn_discussion_resume'] if self.discussion_paused else CONFIG['TEXT']['btn_discussion_pause']
        )
        self.add_log(f"Discussion {'paused' if self.discussion_paused else 'resumed'}", "INFO")
        self._update_discussion_status_display()

    def stop_discussion(self):
        """Request a clean stop — loop thread exits at the next turn boundary"""
        if not self.discussion_running:
            return
        self.discussion_stop_requested = True
        self.discussion_paused = False
        self.add_log("Discussion stop requested", "INFO")

    def _append_discussion_output(self, text):
        """Thread-safe append to the discussion log box — safe to call from
        any thread; the widget mutation always runs on the main thread."""
        def update_widget():
            self.discussion_output.config(state=tk.NORMAL)
            self.discussion_output.insert(tk.END, text)
            self.discussion_output.see(tk.END)
            self.discussion_output.config(state=tk.DISABLED)
        self.root.after(0, update_widget)

    def _update_discussion_status_display(self):
        """Refresh the status label in the Discussion card header"""
        if not hasattr(self, 'discussion_status_label'):
            return

        turns_count = len(self.discussion_transcript)

        if self.discussion_running and self.discussion_paused:
            state_text = CONFIG['TEXT']['status_discussion_paused']
            color = CONFIG['COLORS']['text_muted']
        elif self.discussion_running:
            state_text = CONFIG['TEXT']['status_discussion_running']
            color = CONFIG['COLORS']['status_online']
        else:
            state_text = CONFIG['TEXT']['status_discussion_stopped']
            color = CONFIG['COLORS']['status_offline']

        self.discussion_status_label.config(
            text=f"{state_text} — Round {self.discussion_round} · {turns_count} turns · "
                 f"streak {self.discussion_stall_streak}",
            foreground=color
        )

    def _text_is_near_duplicate(self, text, existing_texts, threshold=None):
        """Deterministic near-duplicate check on plain strings (a participant's turn text) — used
        here as the stall detector: repeated near-identical turns mean the discussion has nothing
        left to say."""
        if threshold is None:
            threshold = CONFIG['DISCUSSION']['dedup_similarity_threshold']
        a = text.lower().strip()
        for b_raw in existing_texts:
            b = b_raw.lower().strip()
            if difflib.SequenceMatcher(None, a, b).ratio() >= threshold:
                return True
        return False

    def _recent_turns_text(self, n=None):
        """Plain-text rendering of the last n raw turns, each attributed to its speaker/role."""
        if n is None:
            n = CONFIG['DISCUSSION']['recent_turns_shown']
        recent = self.discussion_transcript[-n:] if n > 0 else []
        return "\n\n".join(f"[{t['role']} — {t['speaker']}]: {t['text']}" for t in recent)

    def _summarize_discussion(self, max_chars=None):
        """Compact context view fed to each speaker: the condensed summary of everything
        compaction has folded in, followed by the most recent raw turns in full."""
        if max_chars is None:
            max_chars = CONFIG['DISCUSSION']['summary_context_chars']
        parts = []
        if self.discussion_summary:
            summary = self.discussion_summary
            if len(summary) > max_chars:
                summary = summary[-max_chars:]
            parts.append(f"[Summary of earlier discussion]\n{summary}")
        recent = self._recent_turns_text()
        if recent:
            parts.append(recent)
        return "\n\n".join(parts) if parts else "(nothing said yet — you are opening the discussion)"

    def _save_discussion_transcript(self):
        """Write the full raw transcript (plus the condensed summary) to a JSON file in Downloads"""
        if not self.discussion_transcript_path:
            topic_slug = re.sub(r'[^a-zA-Z0-9_-]+', '_', self.discussion_topic or "discussion").strip('_')[:50]
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"discussion_{topic_slug or 'topic'}_{timestamp}.json"
            self.discussion_transcript_path = os.path.join(os.path.expanduser("~"), "Downloads", filename)

        try:
            payload = {
                "topic": self.discussion_topic,
                "round": self.discussion_round,
                "generated": datetime.datetime.now().isoformat(),
                "summary": self.discussion_summary,
                "transcript": self.discussion_transcript,
            }
            with open(self.discussion_transcript_path, 'w', encoding='utf-8') as f:
                json.dump(payload, f, indent=2)
        except Exception as e:
            self.add_log(f"Error saving discussion transcript: {str(e)}", "ERROR")

    def open_discussion_transcript(self):
        """Open the saved transcript JSON in the default viewer"""
        if not self.discussion_transcript_path or not os.path.exists(self.discussion_transcript_path):
            messagebox.showwarning(CONFIG['TEXT']['popup_warning'], CONFIG['TEXT']['msg_no_transcript_yet'])
            return
        try:
            if os.name == 'nt':
                os.startfile(self.discussion_transcript_path)
            else:
                os.system(f'open "{self.discussion_transcript_path}"')
            self.add_log("Opened discussion transcript file", "INFO")
        except Exception as e:
            messagebox.showerror(CONFIG['TEXT']['popup_error'], f"Could not open file:\n{str(e)}")

    def compact_discussion_now(self):
        """Manually trigger a compaction pass on demand, outside the automatic schedule. Requires
        the loop to not be actively running (or to be paused), since compaction replaces
        discussion_summary wholesale."""
        if not self.discussion_transcript:
            messagebox.showwarning(CONFIG['TEXT']['popup_warning'], CONFIG['TEXT']['msg_no_transcript_yet'])
            return

        if self.discussion_running and not self.discussion_paused:
            messagebox.showwarning(CONFIG['TEXT']['popup_warning'], CONFIG['TEXT']['msg_pause_before_compact_discussion'])
            return

        if self.discussion_compacting:
            return  # a manual compaction is already in flight

        # Use whichever participant model is first configured as a stand-in compactor model —
        # compaction doesn't need a dedicated role, just any capable model already in play.
        participants = [p for p in self._collect_discussion_participants() if p["model"] != "None Selected"]
        if not participants:
            messagebox.showwarning(CONFIG['TEXT']['popup_warning'], CONFIG['TEXT']['msg_discussion_too_few'])
            return
        model_key = self.model_display_names.get(participants[0]["model"])
        if not model_key:
            self.add_log(f"Discussion compaction model '{participants[0]['model']}' not found", "ERROR")
            return

        topic = self.discussion_topic or self.discussion_topic_var.get().strip()
        if not topic:
            messagebox.showwarning(CONFIG['TEXT']['popup_warning'], CONFIG['TEXT']['msg_no_discussion_topic'])
            return

        turn_count = len(self.discussion_transcript)
        self.discussion_compacting = True
        self._append_discussion_output(f"\n🔄 Manual compaction requested ({turn_count} turns)...\n")
        self.add_log(f"Manual discussion compaction requested: {turn_count} turns", "INFO")

        def _run():
            try:
                self._ensure_model_active(model_key)
                self._compact_discussion_transcript(topic, model_key)
                self.discussion_last_compaction_round = self.discussion_round
                self.root.after(0, self._append_discussion_output, "  ✓ Manual compaction complete\n")
                self.root.after(0, self._update_discussion_status_display)
            finally:
                self.discussion_compacting = False

        thread = threading.Thread(target=_run, daemon=True)
        thread.start()

    def _maybe_compact_discussion(self, topic, model_key, log):
        """Run a compaction pass if there's enough to condense and this round hasn't already been
        compacted (guards against compacting the same round twice)."""
        if len(self.discussion_transcript) <= CONFIG['DISCUSSION']['recent_turns_shown']:
            return  # nothing sits outside the raw recent-turns window yet — nothing to fold in
        if self.discussion_round == self.discussion_last_compaction_round:
            return

        log(f"\n🔄 Compacting discussion transcript ({len(self.discussion_transcript)} turns)...\n")
        self._ensure_model_active(model_key, log)
        self._compact_discussion_transcript(topic, model_key)
        log("  ✓ Compaction complete\n")
        self.discussion_last_compaction_round = self.discussion_round
        self.root.after(0, self._update_discussion_status_display)

    def _compact_discussion_transcript(self, topic, model_key):
        """Periodic cleanup pass: fold everything except the most recent raw turns into
        discussion_summary, so later speakers get a bounded-size context no matter how long the
        discussion runs."""
        older_turns = self.discussion_transcript[:-CONFIG['DISCUSSION']['recent_turns_shown']] \
            if CONFIG['DISCUSSION']['recent_turns_shown'] > 0 else self.discussion_transcript
        if not older_turns and not self.discussion_summary:
            return

        older_text = "\n\n".join(f"[{t['role']} — {t['speaker']}]: {t['text']}" for t in older_turns)
        full_text = (f"[Summary of earlier discussion]\n{self.discussion_summary}\n\n{older_text}"
                     if self.discussion_summary else older_text)

        prompt = CONFIG['DISCUSSION']['compaction_template'].format(topic=topic, full_transcript=full_text[:8000])

        try:
            response = requests.post(
                f"{self.api_base}{CONFIG['API']['chat_endpoint']}",
                json={
                    "model": model_key,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.3,
                    "max_tokens": 5000
                },
                timeout=self.timeout_var.get()
            )
            if response.status_code != 200:
                self.add_log(f"Discussion compaction API error: {response.status_code}", "WARNING")
                return

            self.discussion_summary = response.json()['choices'][0]['message']['content'].strip()
            self._save_discussion_transcript()
        except Exception as e:
            self.add_log(f"Discussion compaction error: {str(e)}", "ERROR")

    def _get_moderator_synthesis(self, topic, model_key, log):
        """Ask a model to produce the Moderator's closing synthesis over the full discussion.
        Uses DISCUSSION_ROLES['Moderator']['synthesis_template'] — falls back quietly if that
        role's template is ever removed from CONFIG."""
        moderator_cfg = CONFIG['DISCUSSION_ROLES'].get('Moderator', {})
        synthesis_template = moderator_cfg.get('synthesis_template')
        if not synthesis_template:
            return None

        full_transcript = self._summarize_discussion(max_chars=CONFIG['DISCUSSION']['summary_context_chars'] * 2)
        prompt = synthesis_template.format(topic=topic, transcript=full_transcript)

        self._ensure_model_active(model_key, log)
        try:
            response = requests.post(
                f"{self.api_base}{CONFIG['API']['chat_endpoint']}",
                json={
                    "model": model_key,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.4,
                    "max_tokens": CONFIG['DISCUSSION']['moderator_max_tokens']
                },
                timeout=self.timeout_var.get()
            )
            if response.status_code != 200:
                log(f"  ❌ Moderator synthesis API error: {response.status_code}\n")
                self.add_log(f"Moderator synthesis API error: {response.status_code}", "ERROR")
                return None
            return response.json()['choices'][0]['message']['content'].strip()
        except Exception as e:
            log(f"  ❌ Moderator synthesis error: {str(e)}\n")
            self.add_log(f"Moderator synthesis error: {str(e)}", "ERROR")
            return None


    def _discussion_loop(self, topic, participants):
        """Background thread: participants take turns round-robin, each responding to the
        compacted-context + recent-raw-turns view of the discussion so far. Every turn is passed
        through the Constraint Engine and the ticked Discussion Rules. When the discussion
        finishes (it stalls, or you press Stop) the Moderator writes a closing synthesis and —
        if Auto-write is on — the Final Writer writes the finished piece."""
        dcfg = CONFIG['DISCUSSION']
        stall_threshold = dcfg['stall_threshold']

        def log(text):
            self.root.after(0, self._append_discussion_output, text)

        try:
            if self.auto_swap_models_enabled:
                log("\n🔁 Auto load/unload is ON — each model is loaded just before its turn "
                    "and the others are unloaded.\n")
            else:
                log("\n⏭ Auto load/unload is OFF — LM Studio will load each model on first use.\n")
            participant_desc = ", ".join(f"{p['role']} ({p['model_display']})" for p in participants)
            log(f"\n▶ Discussion started — {len(participants)} participants: {participant_desc}\n")

            speaker_idx = 0
            while not self.discussion_stop_requested:
                while self.discussion_paused and not self.discussion_stop_requested:
                    time.sleep(0.5)
                if self.discussion_stop_requested:
                    break

                speaker = participants[speaker_idx % len(participants)]
                speaker_idx += 1
                self.discussion_round += 1

                role_cfg = CONFIG['DISCUSSION_ROLES'].get(speaker["role"])
                if not role_cfg or 'turn_template' not in role_cfg:
                    log(f"  ⚠ Role '{speaker['role']}' has no turn_template — skipping this speaker's turn\n")
                    self.add_log(f"Discussion role '{speaker['role']}' missing turn_template", "WARNING")
                    continue

                log(f"\n▶ Round {self.discussion_round} — {speaker['role']} ({speaker['model_display']})\n")
                self.add_log(f"Discussion round {self.discussion_round}: {speaker['role']} "
                             f"({speaker['model_display']})", "INFO")

                transcript_context = self._summarize_discussion()
                turn_prompt = role_cfg['turn_template'].format(topic=topic, transcript=transcript_context)

                self._ensure_model_active(speaker["model_key"], log)
                response = requests.post(
                    f"{self.api_base}{CONFIG['API']['chat_endpoint']}",
                    json={
                        "model": speaker["model_key"],
                        "messages": [{"role": "user", "content": turn_prompt}],
                        "temperature": 0.7,
                        "max_tokens": dcfg['turn_max_tokens']
                    },
                    timeout=self.timeout_var.get()
                )

                if response.status_code != 200:
                    log(f"  ❌ API error {response.status_code} — skipping this turn\n")
                    self.add_log(f"Discussion API error: {response.status_code}", "ERROR")
                    time.sleep(1)
                    continue

                result = response.json()
                turn_text = result['choices'][0]['message']['content'].strip()

                if 'usage' in result:
                    tokens_used = result['usage'].get('total_tokens', 0)
                    self.session_tokens += tokens_used
                    self.root.after(0, lambda: self.session_tokens_label.config(text=str(self.session_tokens)))

                if not turn_text:
                    log("  — Empty response, skipping\n")
                    continue

                # Run the Constraint Engine on each turn, so a turn with e.g.
                # double spaces or a missing capital gets the same auto-fix treatment.
                if self.engine_loaded and self.engine and self.engine_enabled_var.get():
                    validation_result = self.engine.validate(turn_text, auto_fix=True)
                    if validation_result.get('success') and 'data' in validation_result:
                        turn_text = validation_result['data']
                    elif not validation_result.get('success'):
                        log(f"  ⚠ Constraint validation failed at {validation_result.get('failed_at')} "
                            f"— keeping turn unvalidated\n")

                # Discussion Rules (Double-Check / Verify Claims / Cross-Check) — read fresh every
                # turn, so ticking one mid-run takes effect on the next turn.
                turn_text = self._apply_discussion_rules(turn_text, topic, speaker, log)
                if not turn_text:
                    log("  — Every statement in this turn was removed by the Discussion Rules — skipping it\n")
                    continue

                # Stall detection — near-duplicate of a recent turn means this speaker (or the
                # discussion generally) has nothing new left to say. "Verify Duplicates (strict)"
                # swaps in a lower (stricter) similarity threshold.
                recent_texts = [t['text'] for t in self.discussion_transcript[-6:]]
                strict = (CONFIG['DISCUSSION_RULES']['verify_duplicates']['strict_threshold']
                          if self._discussion_rule_enabled('verify_duplicates') else None)
                is_stalled_turn = self._text_is_near_duplicate(turn_text, recent_texts, threshold=strict)

                # "Investigate Deeper" — only worth an extra call on a turn that added something new
                if not is_stalled_turn and self._discussion_rule_enabled('investigate'):
                    extra = self._run_investigate_followup(turn_text, topic, speaker, log)
                    if extra:
                        turn_text += f"\n\n[Investigated further] {extra}"
                        log("  🔍 Investigate Deeper: added more specific detail to this turn\n")

                log(f"  {turn_text}\n")

                self.discussion_transcript.append({
                    "speaker": speaker["model_display"], "role": speaker["role"],
                    "text": turn_text, "round": self.discussion_round
                })
                self._save_discussion_transcript()

                if is_stalled_turn:
                    self.discussion_stall_streak += 1
                    log(f"  (near-duplicate of a recent turn — stall streak {self.discussion_stall_streak})\n")
                else:
                    self.discussion_stall_streak = 0

                self.root.after(0, self._update_discussion_status_display)

                if self.discussion_stall_streak >= stall_threshold:
                    log(f"\n⏸ {stall_threshold} consecutive near-duplicate turns — auto-pausing "
                        f"(discussion may have run its course)\n")
                    self.add_log("Discussion auto-paused: appears to have stalled", "INFO")
                    self.discussion_paused = True
                    self.discussion_stall_streak = 0
                    self.root.after(0, lambda: self.pause_discussion_btn.config(
                        text=CONFIG['TEXT']['btn_discussion_resume']))
                    self.root.after(0, self._update_discussion_status_display)
                    self._maybe_compact_discussion(topic, speaker["model_key"], log)
                    self._run_moderator_synthesis_if_needed(topic, participants, log)
                    if self.writer_auto_var.get():
                        self._run_final_writer(topic, log, compactor_key=speaker["model_key"])

                compact_every = self._get_discussion_compact_interval()
                if compact_every > 0 and self.discussion_round % compact_every == 0:
                    self._maybe_compact_discussion(topic, speaker["model_key"], log)

                time.sleep(0.3)  # brief pacing between turns

            # Manual/clean stop — one last compaction pass, then the discussion counts as
            # finished: closing synthesis (if a Moderator is taking part) and the Final Writer.
            self._maybe_compact_discussion(topic, participants[0]["model_key"], log)

            log(f"\n■ Discussion stopped at round {self.discussion_round} "
                f"({len(self.discussion_transcript)} total turns saved)\n")
            self.add_log(f"Discussion stopped: {len(self.discussion_transcript)} total turns", "INFO")

            if self.discussion_transcript:
                self._run_moderator_synthesis_if_needed(topic, participants, log)
                if self.writer_auto_var.get():
                    self._run_final_writer(topic, log, compactor_key=participants[0]["model_key"])

        except requests.exceptions.RequestException as e:
            log(f"\n❌ Connection error: {str(e)}\n")
            self.add_log(f"Discussion connection error: {str(e)}", "ERROR")
        except Exception as e:
            log(f"\n❌ Error: {str(e)}\n")
            self.add_log(f"Discussion error: {str(e)}", "ERROR")
        finally:
            self.discussion_running = False
            self.root.after(0, self._update_discussion_status_display)


if __name__ == "__main__":
    root = tk.Tk()
    root.withdraw()
    app = LMStudioOrchestrator(root)
    root.mainloop()

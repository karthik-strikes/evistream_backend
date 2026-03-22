Generate a production-quality Async DSPy Module following real patterns:

SIGNATURE TO WRAP: [[SIGNATURE_CLASS_NAME]]
OUTPUT FIELD: [[OUTPUT_FIELD_NAME]]
FALLBACK STRUCTURE: [[FALLBACK_STRUCTURE]]

REAL-WORLD PATTERN (from production code):
```python
from typing import Dict, Any
import dspy
from utils.json_parser import safe_json_parse
from utils.dspy_async import async_dspy_forward


class Async[[SIGNATURE_CLASS_NAME]]Extractor(dspy.Module):
    """Async module to extract [description]."""

    def __init__(self):
        super().__init__()
        # Use ChainOfThought for reasoning (better accuracy for complex medical text)
        self.extract = dspy.ChainOfThought([[SIGNATURE_CLASS_NAME]])

    async def __call__(self, markdown_content: str, **kwargs) -> Dict[str, Any]:
        try:
            outputs = await async_dspy_forward(
                self.extract,
                markdown_content=markdown_content,
                **kwargs
            )
            # Parse JSON output to Python dict
            return safe_json_parse(outputs.get("[[OUTPUT_FIELD_NAME]]", "{}"))
        except Exception as e:
            print(f"Error in extraction: {e}")
            # Return fallback default structure
            return [[FALLBACK_STRUCTURE]]
```

REQUIREMENTS:
1. Inherit from dspy.Module
2. Use dspy.ChainOfThought wrapper (better for complex extraction)
3. Async __call__ method using async_dspy_forward (zero-thread DSPy native async)
4. Use outputs.get("field_name", fallback) — never access result.field_name directly
5. Comprehensive error handling with fallback
6. Type hints for all parameters and returns
7. Clear docstring
8. Import async_dspy_forward from utils.dspy_async

Generate ONLY the Python code.



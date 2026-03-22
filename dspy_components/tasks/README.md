# Adding New Tasks

To add a new extraction task (schema) to eviStream, follow these steps:

## 1. Create DSPy Components
Create a new directory in `dspy_components/tasks/<task_name>/`.
- **signatures.py**: Define your DSPy signatures.
    - Create individual signatures for specific extraction steps.
    - **Important**: Create a "Combined" or "Record" signature that represents the final output structure of a single record. This is used by the `field_extractor` to generate the schema configuration.
- **modules.py**: Implement the extraction logic.
    - Create a pipeline class (e.g., `Async<TaskName>Pipeline`) that orchestrates the extraction.
    - Ensure it returns a `dspy.Prediction` object.

## 2. Define Schema
Create a new file `schemas/<task_name>.py`.
- Define a `SchemaDefinition` object.
- `signature_class`: Point to your "Combined" or "Record" signature.
- `output_field_name`: The name of the output field in that signature.
- `pipeline_factory`: A function that returns an instance of your pipeline.
- `field_cache_file`: Path where generated field definitions will be stored (e.g., `schemas/generated_fields/<task_name>_fields.json`).

## 3. Register Schema
Update `schemas/registry.py`.
- Import your new schema definition.
- Add it to `_SCHEMA_REGISTRY`.

## 4. Run
You can now run the extraction using the new schema:
```bash
python run.py single --schema <task_name> ...
```

# AF3, DrugLAMP, and Nesso

The registered methods `af3`, `drug_lamp`, and `nesso` run the installed
applications in their own conda environments. Import the classes with:

```python
from rapier.methods import AF3Prediction, DrugLAMP, NessoPrediction
```

Each method screens every ligand independently against one target. The
`protein` input accepts an inline sequence or a FASTA file containing exactly
one nonempty sequence. The `ligands` input accepts individual molecules,
collections, or lists of SMILES/SDF artifacts. IDs come from SMILES names or
SDF titles; repeated IDs have distinct, one-based `input_occurrence` values
across all bound ligand artifacts. Score tables preserve both keys.

See the runnable pipeline examples:

- [AF3](../examples/pipelines/09_af3_prediction.yaml)
- [DrugLAMP](../examples/pipelines/10_drug_lamp_prediction.yaml)
- [Nesso](../examples/pipelines/11_nesso_prediction.yaml)

## Validation lifecycle

`check_inputs(inputs)` runs during task preparation and execution. It checks
sequence contents, FASTA record counts, nonempty libraries, and readable
ligand records using the standard library. It requires no application
environment or GPU on the submission host. DrugLAMP rejects proteins longer
than 1,022 residues and the unsupported residue `J`.

`validate_environment()` checks imports, executables, and the requested GPU
inside the selected conda environment. `check_dependencies(inputs, ctx)`
checks model files, cached encoders, and input-dependent application assets
on the execution host. RDKit then validates each ligand and converts SDF to
isomeric SMILES before inference. DrugLAMP also checks checkpoint tensor
dimensions against its model and ESM layer selection, and rejects ligands
beyond its configured atom limit or SMILES metadata length limit.

Result collection validates each ligand's native outputs. The pooled
`validate_outputs(inputs, outputs, ctx)` hook then checks the complete
exported inventory against the requested inputs, canonical scores, and run
summary before Rapier publishes the results.

`allow_partial_failures` defaults to `true`. Rejected or failed ligands have
explicit reasons in `run_summary.json`, and valid ligands continue. Set it to
`false` to fail the task if any ligand fails. Invalid shared inputs or missing
dependencies fail before inference; zero successful ligands always fails.
Failed tasks retain their files and diagnostics, but publish no validated
artifacts. Each attempt uses fresh case directories to avoid stale results.

## Installed dependencies

Conda must be available in `PATH`. Rapier stays in its own Python environment
and launches scientific commands using `conda run --no-capture-output`.
Rapier does not need to be installed inside the application environments.
`conda_env` accepts an environment name or an absolute/`~`-prefixed environment
directory. Application and asset paths are configurable and expand `~` on
the execution host. Commands preserve paths containing spaces.

These are the defaults discovered on the development host. Package imports
and local assets were checked; GPU inference is a separate validation step.

| Method | Environment / application | Scientific dependencies and assets |
|---|---|---|
| `af3` | `af3_test` (Python 3.12), `~/Applications/alphafold3` | AF3 3.0.1 and compiled extension; JAX/JAXlib 0.8.2, Haiku, NumPy, RDKit, Tokamax. `model_dir` defaults to `~/Applications/model_weights/AF3` containing `af3.bin`. `db_dir` defaults to `~/Applications/model_weights/af3_db`. The data pipeline requires HMMER and databases for the supplied chain types. Inference requires a CUDA GPU. |
| `drug_lamp` | `drug_lamp_test` (Python 3.10), `~/Applications/DrugLAMP` | PyTorch 2.1.2+cu121, DGL 2.4.0+cu121, DGLLife 0.3.2, PyTorch Geometric 2.5.3, PyTorch Lightning 2.0.1.post0, Transformers 4.28.1, fair-esm 2.0.0, RDKit, NumPy, pandas, and the application's model/configuration imports. Checkpoint defaults to `~/Applications/model_weights/DrugLAMP/max_val_ausum= 1.84364.ckpt`. Requires cached ESM2 weights/contact regression and ChemBERTa-77M-MTR weights/config/tokenizer. These working installed versions differ from the application's requirements file. |
| `nesso` | `nesso` (Python 3.11), `nesso predict` | Nesso 1.0.0, PyTorch 2.7.1+cu128, Lightning, RDKit, NumPy, SciPy, Transformers, safetensors, Hugging Face Hub, einops, mashumaro, PyYAML, Click, tqdm. Requires `model.safetensors`, `hparams.json` with an affinity head, `ccd.pkl`, and ESM2-650M weights/tokenizer. `cache` defaults to `~/Applications/model_weights/Nesso`; `model_revision` defaults to `v1.0.0`. |

Existing local assets are used without automatic downloads. Missing assets
produce actionable dependency errors. Populate caches separately when setting
up another host. The adapters set Hugging Face/Transformers offline mode and
check ESM assets before invoking applications.

## AF3

Common configuration: `conda_env`, `application_dir`, `model_dir`, `db_dir`,
`model_seeds` (default `[1]`), `num_diffusion_samples` (default `5`),
`run_data_pipeline` (default `true`), and `allow_partial_failures`.
Default resources: 8 CPUs, 64 GB memory, one GPU, 24 hours.

Alternatively, bind one native AF3 job JSON to `af3_input`, omitting both
`protein` and `ligands`. Official `alphafold3` dialect versions 1–4 are
accepted by the installed parser. Native jobs retain their seeds, chain
definitions, bonds, modifications, DNA/RNA, MSAs, and templates. File
references resolve relative to the original JSON, and the application worker
writes a self-contained staged job before prediction.

With `run_data_pipeline: false`, native protein inputs must explicitly supply
paired/unpaired MSAs and templates; empty strings/lists are allowed. Native
RNA requires an explicit unpaired MSA. Typed protein/ligand inputs in this
mode generate empty MSAs/templates for single-sequence inference.

| Output | Contract |
|---|---|
| `predicted_structures` | All sampled complex CIF files, without duplicating the best model |
| `best_structures` | One highest-ranked CIF per successful job |
| `confidence_scores` | Summary confidence JSON per sampled model |
| `full_confidences` | Atom/token confidence JSON per sampled model |
| `scores` | CSV: `id`, `input_occurrence`, `pose`, `seed`, `sample`, `ranking_score`, `ptm`, `iptm` |
| `run_summary` | JSON with requested/succeeded/failed counts, case IDs, directories, and failure reasons |

All requested seed/sample combinations must be present for a ligand to
succeed. Validation checks input chains, finite coordinates, atom confidence
alignment, square token/chain matrices, confidence ranges, and native ranking
CSV consistency. AF3 rounds summary scores; comparisons allow that rounding.
The best structure must have the selected sample's coordinates and atom
identities. Ranking score, pTM, and ipTM have higher-is-better metadata.

## DrugLAMP

Configuration: `conda_env`, `application_dir`, `checkpoint`, `hf_cache`
(default `~/.cache/huggingface/hub`), `torch_home` (default `~/.cache/torch`),
`model` (`DrugLAMP`, `DrugLAMP2C2P`, or `DrugLAMPwoLLM`), `n_layer`
(`12`, `30`, `33`, `36`, or `48`; default `30`), `device` (`cuda` or `cpu`;
default `cuda`), `batch_size` (default `1`), and `allow_partial_failures`.

| Output | Contract |
|---|---|
| `predictions` | Combined native CSV columns: `SMILES`, `Protein`, `Prediction`, `Predicted_Label` |
| `scores` | CSV: `id`, `input_occurrence`, `interaction_probability`, `predicted_label` |
| `run_summary` | Requested/succeeded/failed inventory and diagnostics |

Native rows must match the submitted protein and ligand. Probabilities must
be finite in `[0, 1]`; labels must equal `int(probability > 0.5)`.
`interaction_probability` has higher-is-better metadata. These are interaction
classification probabilities, without conversion to binding energies.

## Nesso

Configuration: `conda_env`, `cache`, optional `checkpoint` directory and `ccd`
file, `model_revision`, `accelerator` (`gpu` or `cpu`; default `gpu`), optional
`precision` (GPU default `bf16-mixed`, CPU default `32-true`),
`recycling_steps` (default `5`), `num_workers` (default `2`), `seed`
(default `42`), `save_metadata` (default `false`), `no_kernels`
(default `true`), and `allow_partial_failures`.

| Output | Contract |
|---|---|
| `affinity_scores` | Native `affinity.json` per successful ligand |
| `scores` | CSV containing `id`, `input_occurrence`, native affinity and entropy scalars |
| `prediction_tensors` | Optional `predictions.safetensors` per successful ligand when `save_metadata: true` |
| `run_summary` | Requested/succeeded/failed inventory and diagnostics |

Affinity scalars must be finite, binary probabilities must lie in `[0, 1]`,
and native reports must agree with the canonical CSV. Tensor validation checks
payload offsets, required keys (`z`, `pdistogram`, `refine_mask`, `pocket_mask`,
`token_pad_mask`, `mol_type`), and compatible token dimensions. Native affinity
values retain their original scale; `affinity_pred_value` is lower-is-better
and `affinity_probability_binary` is higher-is-better.

Nesso exports affinity and optional tensor representations. It does not export
predicted PDB/mmCIF coordinates.

## Running and checking examples

```bash
python scripts/validate_config.py examples/pipelines/09_af3_prediction.yaml --build
python scripts/validate_config.py examples/pipelines/10_drug_lamp_prediction.yaml --build
python scripts/validate_config.py examples/pipelines/11_nesso_prediction.yaml --build
python -m pytest tests/methods/test_external_predictions.py
```

The examples can be built without scientific dependencies on the submission
host. Execute them on an appropriately configured compute host. Scheduling
overrides go in the method's pipeline `resources` section. CPU inference for
DrugLAMP/Nesso also requires setting `gpus: 0` and a suitable CPU partition.
The initial adapters execute ligand cases serially; batch scheduling and
reusing a loaded model across cases are future performance improvements.

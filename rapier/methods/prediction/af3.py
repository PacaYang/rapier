"""AlphaFold 3 structure prediction with independent ligand screening."""

import csv
import copy
import math
import re
from pathlib import Path

from ...core.specs import ConfigField, EnvironmentSpec, PortSpec, ResourceSpec
from ...data import ComplexStructure, File, ProteinSequence, Report
from ..msa import chain_id, protein_records, read_text, resolve_msas
from ._external import (
    COMMON_CONFIG,
    COMMON_OUTPUTS,
    SCREENING_INPUTS,
    ExternalPrediction,
    expanded,
    file_spec,
    native_case,
    number,
    read_json,
    ligand_inventory,
)


def _protein_records(document):
    return [
        {"chain_id": chain, "sequence": entity["protein"]["sequence"].upper()}
        for entity in document["sequences"]
        if "protein" in entity
        for chain in (
            entity["protein"]["id"]
            if isinstance(entity["protein"]["id"], list)
            else [entity["protein"]["id"]]
        )
    ]


def _apply_options(document, msas, use_templates):
    """Apply authoritative MSAs before inspecting old paths or templates."""
    document = copy.deepcopy(document)
    entities = []
    for entity in document.get("sequences", []):
        if "protein" not in entity:
            entities.append(entity)
            continue
        protein = entity["protein"]
        if not use_templates or msas:
            protein["templates"] = []
        if msas:
            ids = protein["id"] if isinstance(protein["id"], list) else [protein["id"]]
            for chain in ids:
                payload = dict(
                    protein,
                    id=chain,
                    unpairedMsa=msas[chain],
                    pairedMsa="",
                    templates=[],
                )
                payload.pop("unpairedMsaPath", None)
                payload.pop("pairedMsaPath", None)
                entities.append({"protein": payload})
        else:
            entities.append(entity)
    document["sequences"] = entities
    return document


def _chain_ids(document):
    ids = []
    for entity in document["sequences"]:
        if not isinstance(entity, dict) or len(entity) != 1:
            raise ValueError("each AF3 sequence must describe exactly one entity")
        kind, payload = next(iter(entity.items()))
        if kind not in ("protein", "rna", "dna", "ligand") or not isinstance(
            payload, dict
        ):
            raise ValueError("unsupported AF3 entity")
        chain = payload.get("id")
        values = chain if isinstance(chain, list) else [chain]
        if not values or any(
            not isinstance(value, str) or not re.fullmatch("[A-Z]+", value)
            for value in values
        ):
            raise ValueError("AF3 chain IDs must be nonempty uppercase letter strings")
        ids.extend(values)
        if kind == "ligand":
            if ("smiles" in payload) == ("ccdCodes" in payload):
                raise ValueError(
                    "AF3 ligand requires exactly one of smiles or ccdCodes"
                )
        elif (
            not isinstance(payload.get("sequence"), str)
            or not payload["sequence"].strip()
        ):
            raise ValueError(f"AF3 {kind} requires a nonempty sequence")
    if len(ids) != len(set(ids)):
        raise ValueError("AF3 chain IDs must be unique")
    return ids


def _check_document(document, directory, run_data_pipeline):
    if not isinstance(document, dict):
        raise ValueError("af3_input must contain one JSON job object")
    if (
        document.get("dialect") != "alphafold3"
        or type(document.get("version")) is not int
        or document["version"] not in (1, 2, 3, 4)
    ):
        raise ValueError(
            "af3_input requires dialect alphafold3 and version 1, 2, 3, or 4"
        )
    if not isinstance(document.get("name"), str) or not document["name"].strip():
        raise ValueError("AF3 job name cannot be empty")
    seeds = document.get("modelSeeds")
    if (
        not isinstance(seeds, list)
        or not seeds
        or any(type(seed) is not int or seed < 0 for seed in seeds)
        or len(set(seeds)) != len(seeds)
    ):
        raise ValueError("AF3 modelSeeds must contain distinct nonnegative integers")
    if not isinstance(document.get("sequences"), list) or not document["sequences"]:
        raise ValueError("AF3 requires at least one sequence/entity")
    _chain_ids(document)

    def references(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key in (
                    "unpairedMsaPath",
                    "pairedMsaPath",
                    "mmcifPath",
                    "userCCDPath",
                ):
                    if not isinstance(item, str) or not item:
                        raise ValueError(f"{key} must be a file path")
                    path = Path(item).expanduser()
                    path = path if path.is_absolute() else directory / path
                    if not path.is_file() or not path.stat().st_size:
                        raise ValueError(
                            f"AF3 referenced file is missing or empty: {path}"
                        )
                else:
                    references(item)
        elif isinstance(value, list):
            for item in value:
                references(item)

    references(document)
    if not run_data_pipeline:
        for entity in document["sequences"]:
            payload = entity.get("protein") or entity.get("rna")
            if payload is None:
                continue
            keys = (
                ("unpairedMsa", "pairedMsa")
                if "protein" in entity
                else ("unpairedMsa",)
            )
            if any(
                payload.get(key) is None and payload.get(key + "Path") is None
                for key in keys
            ):
                raise ValueError(
                    "Prepared AF3 inputs require explicit MSA values or paths; "
                    "empty strings are allowed"
                )
            if "protein" in entity and not isinstance(payload.get("templates"), list):
                raise ValueError(
                    "Prepared AF3 protein input requires templates "
                    "(an empty list is allowed)"
                )


def _cif_atoms(path):
    """Read the atom_site loop, leaving unrelated mmCIF text blocks alone."""
    lines = Path(path).read_text().splitlines()
    atoms, columns, pending, active = [], [], [], False
    for line in lines:
        line = line.strip()
        if line.startswith("_atom_site."):
            columns.append(line.split()[0])
            active = True
            continue
        if not active:
            continue
        if not line or line.startswith("#") or line.startswith(("loop_", "_")):
            if atoms or pending:
                break
            continue
        pending.extend(
            token.strip("\"'") for token in re.findall(r"'[^']*'|\"[^\"]*\"|\S+", line)
        )
        while len(pending) >= len(columns):
            row, pending = (
                dict(zip(columns, pending[: len(columns)])),
                pending[len(columns) :],
            )
            coords = tuple(
                number(row["_atom_site.Cartn_" + axis], "CIF coordinate")
                for axis in "xyz"
            )
            atoms.append(
                (
                    row["_atom_site.label_asym_id"],
                    row.get("_atom_site.label_atom_id", ""),
                    coords,
                )
            )
    if not atoms or pending:
        raise ValueError(f"CIF has no complete readable atom_site coordinates: {path}")
    return atoms


def _confidence(summary, full, atoms, chains):
    ranking = number(summary["ranking_score"], "ranking_score")
    for key in ("ptm", "iptm", "fraction_disordered", "has_clash"):
        value = summary.get(key)
        if value is None and key == "iptm" and len(chains) == 1:
            continue
        number(value, key, 0, 1)
    if full["atom_chain_ids"] != [atom[0] for atom in atoms] or len(
        full["atom_plddts"]
    ) != len(atoms):
        raise ValueError("AF3 atom confidence inventory does not match the CIF")
    for value in full["atom_plddts"]:
        number(value, "atom_plddts", 0, 100)
    tokens = full["token_chain_ids"]
    if (
        not tokens
        or len(tokens) != len(full["token_res_ids"])
        or set(tokens) != set(chains)
    ):
        raise ValueError("AF3 token confidence inventory does not match input chains")
    if any(type(value) is not int or value < 1 for value in full["token_res_ids"]):
        raise ValueError("AF3 token residue IDs must be positive integers")
    for key, maximum in (("pae", None), ("contact_probs", 1)):
        matrix = full[key]
        if len(matrix) != len(tokens) or any(len(row) != len(tokens) for row in matrix):
            raise ValueError(f"AF3 {key} must be a square token matrix")
        available = False
        for row in matrix:
            for value in row:
                if value is not None:  # AF3 serializes unavailable entries as null.
                    number(value, key, 0, maximum)
                    available = True
        if not available:
            raise ValueError(f"AF3 {key} contains no available numeric confidences")
    for key in ("chain_ptm", "chain_iptm", "chain_pair_pae_min", "chain_pair_iptm"):
        values = summary[key]
        if len(values) != len(chains):
            raise ValueError(f"AF3 {key} has an incorrect chain dimension")
        for value in values:
            items = value if isinstance(value, list) else [value]
            if key.startswith("chain_pair") and len(items) != len(chains):
                raise ValueError(f"AF3 {key} must be a square chain matrix")
            for item in items:
                if item is not None:
                    number(item, key, 0, None if key.endswith("pae_min") else 1)
    return ranking


class AF3Prediction(ExternalPrediction):
    """Predict structures with AlphaFold 3, one independent job per ligand."""

    register_name = "af3"
    display_name = "AlphaFold 3"
    category = "prediction"
    version = "3.0.1"
    inputs = {
        "proteins": PortSpec(
            ProteinSequence, ("inline", "fasta"), required=False, cardinality="many"
        ),
        "ligands": PortSpec(
            SCREENING_INPUTS["ligands"].types,
            ("smiles", "sdf"),
            required=False,
            cardinality="many",
        ),
        "af3_input": PortSpec(File, "json", required=False),
        "msa": PortSpec(File, "a3m", required=False, cardinality="many"),
    }
    outputs = {
        **COMMON_OUTPUTS,
        "predicted_structures": PortSpec(ComplexStructure, "cif", cardinality="many"),
        "best_structures": PortSpec(ComplexStructure, "cif", cardinality="many"),
        "confidence_scores": PortSpec(Report, "json", cardinality="many"),
        "full_confidences": PortSpec(Report, "json", cardinality="many"),
    }
    resources = ResourceSpec(
        cpus=8, memory="64GB", gpus=1, time="24:00:00", partition="gpu"
    )
    environment = EnvironmentSpec(
        conda_env="af3_test",
        executables=["python"],
        python_packages=[
            "alphafold3",
            "alphafold3.cpp",
            "jax",
            "jaxlib",
            "haiku",
            "numpy",
            "rdkit",
            "tokamax",
            "absl",
            "tqdm",
            "zstandard",
            "scipy",
        ],
    )
    config_fields = {
        **COMMON_CONFIG,
        "conda_env": ConfigField(str, default="af3_test"),
        "application_dir": ConfigField(str, default="~/Applications/alphafold3"),
        "model_dir": ConfigField(str, default="~/Applications/model_weights/AF3"),
        "db_dir": ConfigField(str, default="~/Applications/model_weights/af3_db"),
        "model_seeds": ConfigField(list, default=[1]),
        "num_diffusion_samples": ConfigField(int, default=5, minimum=1),
        "run_data_pipeline": ConfigField(bool, default=True),
        "use_templates": ConfigField(bool, default=True),
    }
    score_directions = {"ranking_score": "higher", "ptm": "higher", "iptm": "higher"}

    def check_config(self, config):
        seeds = config["model_seeds"]
        return super().check_config(config) + (
            ["model_seeds must contain distinct nonnegative integers"]
            if not seeds
            or any(type(v) is not int or v < 0 for v in seeds)
            or len(set(seeds)) != len(seeds)
            else []
        )

    def validate_bindings(self, bound_ports):
        bound = set(bound_ports)
        if "af3_input" in bound:
            return (
                ["af3_input is mutually exclusive with proteins and ligands"]
                if bound & {"proteins", "ligands"}
                else []
            )
        return [
            f"{port} input is required"
            for port in ("proteins", "ligands")
            if port not in bound
        ]

    def _native(self, inputs):
        artifact = inputs["af3_input"]
        source = Path(artifact.metadata.get("staged_from", artifact.path)).resolve()
        document = read_json(source)
        # Check the entity inventory before splitting shared protein IDs.
        _chain_ids(document)
        msas = self._msa_texts(inputs, _protein_records(document))
        return _apply_options(document, msas, self.config["use_templates"]), source

    def _msa_texts(self, inputs, records):
        if not inputs.get("msa"):
            return {}
        if not records:
            raise ValueError("MSA input requires at least one protein chain")
        return {
            chain: read_text(artifact)
            for chain, artifact in resolve_msas(inputs["msa"], records).items()
        }

    def _protein_input(self, inputs):
        return protein_records(inputs["proteins"]) if inputs.get("proteins") else None

    def _inventory(self, inputs):
        if inputs.get("af3_input"):
            try:
                document, _ = self._native(inputs)
                return [native_case(document["name"])], []
            except Exception as exc:
                return [], [f"af3_input: {exc}"]
        return super()._inventory(inputs)

    def check_inputs(self, inputs):
        try:
            if inputs.get("af3_input"):
                document, source = self._native(inputs)
                _check_document(
                    document, source.parent, self.config["run_data_pipeline"]
                )
            else:
                records = protein_records(inputs.get("proteins"))
                self._msa_texts(inputs, records)
        except Exception as exc:
            return [f"AF3 input: {exc}"]
        if inputs.get("af3_input"):
            return []
        cases, errors = ligand_inventory(inputs)
        bad = [
            f"ligand {case['id']}: {case['error']}"
            for case in cases
            if case.get("error")
        ]
        if (
            not self.config["allow_partial_failures"]
            or cases
            and len(bad) == len(cases)
        ):
            errors.extend(bad)
        return errors

    def _searches(self, inputs):
        if not self.config["run_data_pipeline"]:
            return set()
        if inputs.get("af3_input"):
            document, _ = self._native(inputs)
        else:
            protein = {}
            if inputs.get("msa"):
                protein.update(unpairedMsa="", pairedMsa="", templates=[])
            elif not self.config["use_templates"]:
                protein["templates"] = []
            document = {"sequences": [{"protein": protein}]}
        searches = set()
        for entity in document["sequences"]:
            if "protein" in entity:
                protein = entity["protein"]
                protein_msa_missing = any(
                    protein.get(key) is None and protein.get(key + "Path") is None
                    for key in ("unpairedMsa", "pairedMsa")
                )
                if protein_msa_missing:
                    searches.add("protein")
                if protein.get("templates") is None:
                    searches.add("templates")
                    # Existing explicit MSAs can seed template search directly;
                    # only run the sequence search when those MSAs are absent.
                    if protein_msa_missing:
                        searches.add("protein")
            elif (
                "rna" in entity
                and entity["rna"].get("unpairedMsa") is None
                and entity["rna"].get("unpairedMsaPath") is None
            ):
                searches.add("rna")
        return searches

    def _required_paths(self, inputs):
        paths = [
            (
                "AF3 script",
                expanded(self.config["application_dir"]) / "run_alphafold.py",
                False,
            ),
            ("AF3 weights", expanded(self.config["model_dir"]) / "af3.bin", False),
        ]
        if self.config["run_data_pipeline"]:
            kinds = self._searches(inputs)
            names = []
            if "protein" in kinds:
                names += [
                    "bfd-first_non_consensus_sequences.fasta",
                    "mgy_clusters_2022_05.fa",
                    "uniprot_all_2021_04.fa",
                    "uniref90_2022_05.fa",
                ]
            if "templates" in kinds:
                names += ["pdb_seqres_2022_09_28.fasta", "mmcif_files"]
            if "rna" in kinds:
                names += [
                    "nt_rna_2023_02_23_clust_seq_id_90_cov_80_rep_seq.fasta",
                    "rfam_14_9_clust_seq_id_90_cov_80_rep_seq.fasta",
                    "rnacentral_active_seq_id_90_cov_80_linclust.fasta",
                ]
            paths += [
                (
                    "AF3 database",
                    expanded(self.config["db_dir"]) / name,
                    name == "mmcif_files",
                )
                for name in names
            ]
        return paths

    def check_dependencies(self, inputs, ctx):
        errors = super().check_dependencies(inputs, ctx)
        searches = self._searches(inputs)
        if searches:
            request = {
                "action": "probe",
                "modules": [],
                "executables": sorted(
                    ({"jackhmmer"} if "protein" in searches else set())
                    | ({"nhmmer", "hmmalign"} if "rna" in searches else set())
                    | (
                        {"hmmsearch", "hmmbuild", "hmmalign"}
                        if "templates" in searches
                        else set()
                    )
                ),
                "gpu": False,
            }
            try:
                self._worker(request, ctx.work_dir)
            except Exception as exc:
                errors.append(str(exc))
        if inputs.get("af3_input"):
            try:
                self._worker(
                    {
                        "action": "af3_input",
                        "source": str(self._native(inputs)[1]),
                        "run_data_pipeline": bool(searches),
                        "msa": self._msa_texts(
                            inputs, _protein_records(self._native(inputs)[0])
                        ),
                        "use_templates": self.config["use_templates"]
                        and not bool(inputs.get("msa")),
                    },
                    ctx.work_dir,
                )
            except Exception as exc:
                errors.append(str(exc))
        return errors

    def _prepare_cases(self, cases, directory):
        return (
            cases
            if "smiles" not in cases[0]
            else super()._prepare_cases(cases, directory)
        )

    def run(self, inputs, ctx):
        self._native_source = (
            str(self._native(inputs)[1]) if inputs.get("af3_input") else None
        )
        records = (
            _protein_records(self._native(inputs)[0])
            if self._native_source
            else self._protein_input(inputs)
        )
        self._supplied_msas = self._msa_texts(inputs, records)
        self._run_data_pipeline = bool(self._searches(inputs))
        return super().run(inputs, ctx)

    def _predict_case(self, case, directory, protein):
        request = {
            "action": "af3_input",
            "run_data_pipeline": self._run_data_pipeline,
            "destination": str(directory / "input.json"),
            "msa": self._supplied_msas,
            "use_templates": self.config["use_templates"]
            and not bool(self._supplied_msas),
        }
        if self._native_source:
            request["source"] = self._native_source
        else:
            sequences = []
            for record in protein:
                chain = {"id": record["chain_id"], "sequence": record["sequence"]}
                if not self.config["run_data_pipeline"]:
                    chain.update(unpairedMsa="", pairedMsa="", templates=[])
                elif not request["use_templates"]:
                    chain["templates"] = []
                sequences.append({"protein": chain})
            used = {record["chain_id"] for record in protein}
            ligand_index = 0
            while chain_id(ligand_index) in used:
                ligand_index += 1
            sequences.append(
                {
                    "ligand": {
                        "id": chain_id(ligand_index),
                        "smiles": case["prepared_smiles"],
                    }
                }
            )
            request["document"] = {
                "dialect": "alphafold3",
                "version": 4,
                "name": case["case_id"],
                "modelSeeds": self.config["model_seeds"],
                "sequences": sequences,
            }
        self._worker(request, directory)
        self._command(
            [
                "python",
                "-B",
                str(expanded(self.config["application_dir"]) / "run_alphafold.py"),
                "--json_path",
                str(directory / "input.json"),
                "--output_dir",
                str(directory / "native"),
                "--model_dir",
                str(expanded(self.config["model_dir"])),
                "--db_dir",
                str(expanded(self.config["db_dir"])),
                f"--run_data_pipeline={str(self._run_data_pipeline).lower()}",
                "--num_diffusion_samples",
                str(self.config["num_diffusion_samples"]),
            ],
            directory,
            "af3",
        )

    def _collect_case(self, case, directory, protein):
        document = read_json(directory / "input.json")
        chains = _chain_ids(document)
        if protein is not None:
            if (
                document["modelSeeds"] != self.config["model_seeds"]
                or _protein_records(document)
                != [
                    {"chain_id": record["chain_id"], "sequence": record["sequence"]}
                    for record in protein
                ]
                or [
                    entity["ligand"]["smiles"]
                    for entity in document["sequences"]
                    if "ligand" in entity
                ]
                != [case["prepared_smiles"]]
            ):
                raise ValueError("AF3 staged input does not match the requested pair")
        elif getattr(self, "_native_source", None):
            original = read_json(self._native_source)
            if (
                document["modelSeeds"] != original["modelSeeds"]
                or document["name"] != original["name"]
                or chains != _chain_ids(original)
            ):
                raise ValueError("AF3 staged input does not match the native job")
        ranking_paths = list((directory / "native").glob("*/*_ranking_scores.csv"))
        if len(ranking_paths) != 1:
            raise ValueError("AF3 must produce exactly one native ranking CSV")
        ranking_path = ranking_paths[0]
        job = ranking_path.name[: -len("_ranking_scores.csv")]
        root = ranking_path.parent
        with ranking_path.open(newline="") as handle:
            rankings = list(csv.DictReader(handle))
        expected = {
            (seed, sample)
            for seed in document["modelSeeds"]
            for sample in range(self.config["num_diffusion_samples"])
        }
        seen, files, rows, models, errors = set(), [], [], [], []
        for pose, item in enumerate(rankings, 1):
            try:
                seed, sample = int(item["seed"]), int(item["sample"])
                if (seed, sample) not in expected or (seed, sample) in seen:
                    raise ValueError("duplicate or unexpected AF3 seed/sample")
                seen.add((seed, sample))
                score = number(item["ranking_score"], "ranking_score")
                prefix = (
                    root
                    / f"seed-{seed}_sample-{sample}"
                    / f"{job}_seed-{seed}_sample-{sample}"
                )
                structure, summary_path, full_path = [
                    Path(str(prefix) + suffix)
                    for suffix in (
                        "_model.cif",
                        "_summary_confidences.json",
                        "_confidences.json",
                    )
                ]
                atoms = _cif_atoms(structure)
                if set(atom[0] for atom in atoms) != set(chains):
                    raise ValueError("AF3 model does not contain all input chains")
                summary, full = read_json(summary_path), read_json(full_path)
                report_score = _confidence(summary, full, atoms, chains)
                if not math.isclose(score, report_score, abs_tol=0.00501):
                    raise ValueError(
                        "AF3 ranking CSV disagrees with summary confidence"
                    )
                meta = {
                    "id": case["id"],
                    "input_occurrence": case["input_occurrence"],
                    "pose": pose,
                    "seed": seed,
                    "sample": sample,
                }
                files += [
                    file_spec(port, path, **meta)
                    for port, path in (
                        ("predicted_structures", structure),
                        ("confidence_scores", summary_path),
                        ("full_confidences", full_path),
                    )
                ]
                rows.append(
                    {
                        **meta,
                        "ranking_score": score,
                        "ptm": summary["ptm"],
                        "iptm": summary.get("iptm"),
                    }
                )
                models.append((score, atoms, meta))
            except Exception as exc:
                errors.append(str(exc))
        if seen != expected:
            errors.append(
                "AF3 model inventory does not match "
                "requested seeds and diffusion samples"
            )
        if errors:
            raise ValueError("; ".join(errors))
        best = max(models, key=lambda model: model[0])
        best_path = root / f"{job}_model.cif"
        expected_paths = {
            Path(item["path"])
            for item in files
            if item["port"] == "predicted_structures"
        } | {best_path}
        if set(root.rglob("*_model.cif")) != expected_paths:
            raise ValueError("AF3 produced unexpected or unranked model structures")
        if _cif_atoms(best_path) != best[1]:
            raise ValueError(
                "AF3 best structure does not match the highest ranked model"
            )
        best_summary = read_json(root / f"{job}_summary_confidences.json")
        _confidence(
            best_summary, read_json(root / f"{job}_confidences.json"), best[1], chains
        )
        if not math.isclose(
            number(best_summary["ranking_score"], "best ranking_score"),
            best[0],
            abs_tol=0.00501,
        ):
            raise ValueError("AF3 best summary disagrees with the selected model")
        files.append(file_spec("best_structures", best_path, **best[2]))
        return {"rows": rows, "files": files}

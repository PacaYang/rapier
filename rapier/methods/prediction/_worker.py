"""Standalone scientific preflight worker; does not import Rapier.

Invoked by absolute path inside each application's conda environment. The last
marked stdout line is the JSON response; application logging is left intact.
"""

import importlib
import json
import shutil
import sys
from pathlib import Path

# Executing this file by path puts its directory first on sys.path. Remove it
# so Rapier's sibling nesso.py cannot shadow the installed nesso package.
_SCRIPT_DIR = Path(__file__).resolve().parent
sys.path[:] = [entry for entry in sys.path if Path(entry).resolve() != _SCRIPT_DIR]


def probe(request):
    if request.get("application_dir"):
        sys.path.insert(0, str(Path(request["application_dir"]).expanduser().resolve()))
    errors, versions = [], {}
    for name in request["modules"]:
        try:
            module = importlib.import_module(name)
            versions[name] = str(getattr(module, "__version__", "available"))
        except Exception as exc:
            errors.append(f"Cannot import {name}: {exc}")
    for name in request.get("executables", []):
        if not shutil.which(name):
            errors.append(
                f"Required executable not found in application environment: {name}"
            )
    if request.get("gpu") and not errors:
        if request["tool"] == "af3":
            import jax

            available = any(device.platform == "gpu" for device in jax.devices())
        else:
            import torch

            available = torch.cuda.is_available()
        if not available:
            errors.append(
                "The selected GPU mode requires an available CUDA GPU "
                "on the execution host"
            )
    return {"errors": errors, "versions": versions}


def prepare_ligands(request):
    from rdkit import Chem

    cases = []
    for original in request["cases"]:
        case = dict(original)
        try:
            if case.get("error"):
                raise ValueError(case["error"])
            if case.get("block"):
                mol = Chem.MolFromMolBlock(case["block"], sanitize=True, removeHs=False)
            else:
                mol = Chem.MolFromSmiles(case["smiles"])
            if mol is None or not mol.GetNumHeavyAtoms():
                raise ValueError("RDKit could not parse a ligand with heavy atoms")
            mol = Chem.RemoveHs(mol)
            if request.get("max_atoms") and mol.GetNumAtoms() > request["max_atoms"]:
                raise ValueError(
                    f"ligand exceeds model limit of {request['max_atoms']} atoms"
                )
            case["prepared_smiles"] = Chem.MolToSmiles(mol, isomericSmiles=True)
            if (
                request.get("max_smiles_length")
                and len(case["prepared_smiles"]) > request["max_smiles_length"]
            ):
                raise ValueError(
                    "SMILES exceeds DrugLAMP's output metadata limit; "
                    "refusing silent truncation"
                )
        except Exception as exc:
            case["error"] = str(exc)
        cases.append(case)
    return {"cases": cases}


def prepare_af3(request):
    from alphafold3.common import folding_input

    source = Path(request["source"]) if request.get("source") else None
    payload = source.read_text() if source else json.dumps(request["document"])
    job = folding_input.Input.from_json(payload, json_path=source)
    document = json.loads(job.to_json())
    if not request["run_data_pipeline"]:
        for entity in document["sequences"]:
            protein = entity.get("protein")
            rna = entity.get("rna")
            if protein is not None and any(
                protein.get(key) is None
                for key in ("unpairedMsa", "pairedMsa", "templates")
            ):
                raise ValueError(
                    "AF3 inference without the data pipeline requires explicit "
                    "protein MSAs and templates (empty values are allowed)"
                )
            if rna is not None and rna.get("unpairedMsa") is None:
                raise ValueError(
                    "AF3 inference without the data pipeline requires "
                    "explicit RNA unpairedMsa"
                )
    if request.get("destination"):
        Path(request["destination"]).write_text(json.dumps(document, indent=2) + "\n")
    return {"document": document}


def drug_lamp_assets(request):
    import torch
    from transformers import AutoConfig, AutoTokenizer

    application = Path(request["application_dir"]).expanduser().resolve()
    sys.path.insert(0, str(application))
    from configs import get_cfg_defaults
    from model import MInterface

    cfg = get_cfg_defaults()
    cfg.merge_from_file(str(application / "configs" / (request["model"] + ".yaml")))
    hub = str(Path(request["hf_cache"]).expanduser().resolve())
    drug_config = AutoConfig.from_pretrained(
        "DeepChem/ChemBERTa-77M-MTR", cache_dir=hub, local_files_only=True
    )
    AutoTokenizer.from_pretrained(
        "DeepChem/ChemBERTa-77M-MTR", cache_dir=hub, local_files_only=True
    )
    # Check model weights without constructing or downloading the encoder.
    from huggingface_hub import hf_hub_download

    hf_hub_download(
        "DeepChem/ChemBERTa-77M-MTR",
        "pytorch_model.bin",
        cache_dir=hub,
        local_files_only=True,
    )
    torch_cache = (
        Path(request["torch_home"]).expanduser().resolve() / "hub" / "checkpoints"
    )
    model_sizes = {12: "35M", 30: "150M", 33: "650M", 36: "3B", 48: "15B"}
    stem = f"esm2_t{request['n_layer']}_{model_sizes[request['n_layer']]}_UR50D"
    for suffix in (".pt", "-contact-regression.pt"):
        path = torch_cache / (stem + suffix)
        if not path.is_file() or path.stat().st_size == 0:
            raise ValueError(f"Missing cached ESM2 asset: {path}")
    dimensions = {12: 480, 30: 640, 33: 1280, 36: 2560, 48: 5120}
    model = MInterface(request["model"], cfg).load_model(
        n_drug_feature=drug_config.hidden_size,
        n_prot_feature=dimensions[request["n_layer"]],
    )
    checkpoint = torch.load(
        str(Path(request["checkpoint"]).expanduser()),
        map_location="cpu",
        weights_only=True,
    )
    state = checkpoint.get("state_dict", checkpoint)
    state = {key.removeprefix("exp_model."): value for key, value in state.items()}
    expected = model.state_dict()
    for key, tensor in expected.items():
        if key not in state or state[key].shape != tensor.shape:
            raise ValueError(
                f"Checkpoint is incompatible with {request['model']} / "
                f"ESM2 layer {request['n_layer']}: {key}"
            )
    return {"max_atoms": int(cfg.DRUG.MAX_NODES)}


def nesso_assets(request):
    from huggingface_hub import hf_hub_download, snapshot_download

    cache = Path(request["cache"]).expanduser().resolve() / "huggingface"
    revision = request["model_revision"]
    checkpoint = request.get("checkpoint")
    ccd = request.get("ccd")
    if not checkpoint:
        weights = hf_hub_download(
            "recursionpharma/nesso",
            f"{revision}/model.safetensors",
            revision=revision,
            cache_dir=str(cache),
            local_files_only=True,
        )
        checkpoint = str(Path(weights).parent)
        hf_hub_download(
            "recursionpharma/nesso",
            f"{revision}/hparams.json",
            revision=revision,
            cache_dir=str(cache),
            local_files_only=True,
        )
    checkpoint = Path(checkpoint).expanduser().resolve()
    for name in ("model.safetensors", "hparams.json"):
        if not (checkpoint / name).is_file() or not (checkpoint / name).stat().st_size:
            raise ValueError(f"Missing Nesso checkpoint asset: {checkpoint / name}")
    hparams = json.loads((checkpoint / "hparams.json").read_text())
    if not hparams.get("affinity_prediction"):
        raise ValueError("Nesso checkpoint must contain an affinity head")
    if not ccd:
        ccd = hf_hub_download(
            "recursionpharma/nesso",
            "ccd.pkl",
            revision=revision,
            cache_dir=str(cache),
            local_files_only=True,
        )
    ccd = Path(ccd).expanduser().resolve()
    if not ccd.is_file() or not ccd.stat().st_size:
        raise ValueError(f"Missing CCD asset: {ccd}")
    esm = Path(
        snapshot_download(
            "facebook/esm2_t33_650M_UR50D", cache_dir=str(cache), local_files_only=True
        )
    )
    for name in (
        "config.json",
        "model.safetensors",
        "tokenizer_config.json",
        "vocab.txt",
    ):
        if not (esm / name).is_file():
            raise ValueError(f"Missing cached Nesso ESM2 asset: {esm / name}")
    return {"checkpoint": str(checkpoint), "ccd": str(ccd)}


def validate_nesso(request):
    from nesso.data.yaml_input import validate_schema

    validate_schema(request["document"])
    return {}


def main():
    actions = {
        "probe": probe,
        "ligands": prepare_ligands,
        "af3_input": prepare_af3,
        "drug_lamp_assets": drug_lamp_assets,
        "nesso_assets": nesso_assets,
        "nesso_input": validate_nesso,
    }
    try:
        request = json.load(sys.stdin)
        response = actions[request["action"]](request)
    except Exception as exc:
        response = {"errors": [f"{type(exc).__name__}: {exc}"]}
    print("RAPIER_RESULT=" + json.dumps(response))


if __name__ == "__main__":
    main()

"""Similarity search method for finding similar molecules.

Computes molecular fingerprints and keeps database molecules whose Tanimoto
similarity to any query reaches a threshold.
"""

from typing import Any, Dict

from ...core.method import Method, MethodError, RunContext
from ...core.specs import ConfigField, EnvironmentSpec, PortSpec, ResourceSpec
from ...data import Molecules, Scores
from ..utils import rdkit_molecules, write_rdkit_sdf


class SimilaritySearch(Method):
    """Find molecules similar to query structures.

    Uses Morgan, MACCS or RDKit fingerprints and Tanimoto similarity.
    ``similar_molecules`` holds each matching database molecule once (ranked by
    its best similarity); ``similarity_scores`` has one row per
    ``(id, query_id)`` pair above the threshold.

    Parameters:
        - fingerprint_type: Type of fingerprint ('morgan', 'maccs', 'rdkit')
        - radius: Radius for Morgan fingerprints (default: 2)
        - n_bits: Number of bits for fingerprints (default: 2048)
        - similarity_threshold: Minimum Tanimoto similarity (0-1, default: 0.7)
        - max_results: Maximum (molecule, query) pairs kept (default: 100)
    """

    register_name = "similarity_search"
    display_name = "Similarity Search"
    category = "screening"
    inputs = {
        "queries": PortSpec(Molecules, ("smiles", "sdf"), description="Query molecules"),
        "database": PortSpec(Molecules, ("sdf", "smiles"), description="Molecule database"),
    }
    outputs = {
        "similar_molecules": PortSpec(Molecules, "sdf", description="Database molecules above the threshold"),
        "similarity_scores": PortSpec(Scores, "csv", description="id, query_id, similarity"),
    }
    config_fields = {
        "fingerprint_type": ConfigField(str, default="morgan", choices=("morgan", "maccs", "rdkit")),
        "radius": ConfigField(int, default=2, minimum=1),
        "n_bits": ConfigField(int, default=2048, minimum=1),
        "similarity_threshold": ConfigField((int, float), default=0.7, minimum=0, maximum=1),
        "max_results": ConfigField(int, default=100, minimum=1),
    }
    resources = ResourceSpec(cpus=4, memory="8GB", time="02:00:00", partition="cpu")
    environment = EnvironmentSpec(python_packages=["rdkit"])

    def _fingerprint(self, mol):
        from rdkit import Chem
        from rdkit.Chem import MACCSkeys, rdFingerprintGenerator

        kind = self.config["fingerprint_type"]
        if kind == "morgan":
            generator = rdFingerprintGenerator.GetMorganGenerator(
                radius=self.config["radius"], fpSize=self.config["n_bits"]
            )
            return generator.GetFingerprint(mol)
        if kind == "maccs":
            return MACCSkeys.GenMACCSKeys(mol)
        return Chem.RDKFingerprint(mol, fpSize=self.config["n_bits"])

    def run(self, inputs: Dict[str, Any], ctx: RunContext) -> Dict[str, Any]:
        from rdkit.Chem import DataStructs

        queries = [(record, self._fingerprint(mol)) for record, mol in rdkit_molecules(inputs["queries"]) if mol]
        if not queries:
            raise MethodError("No valid query molecules")
        pairs = []
        molecules = {}
        for record, mol in rdkit_molecules(inputs["database"]):
            if mol is None:
                continue
            fingerprint = self._fingerprint(mol)
            for query, query_fp in queries:
                similarity = DataStructs.TanimotoSimilarity(query_fp, fingerprint)
                if similarity >= self.config["similarity_threshold"]:
                    pairs.append({"id": record.id, "query_id": query.id, "similarity": round(similarity, 4)})
                    molecules.setdefault(record.id, mol)
        pairs.sort(key=lambda row: row["similarity"], reverse=True)
        pairs = pairs[: self.config["max_results"]]

        kept = []
        for row in pairs:  # Best similarity first, each molecule once.
            mol = molecules.pop(row["id"], None)
            if mol is not None:
                mol.SetProp("query_id", row["query_id"])
                mol.SetProp("similarity", f"{row['similarity']:.3f}")
                kept.append(mol)
        return {
            "similar_molecules": write_rdkit_sdf(kept, ctx.output_dir / "similar.sdf"),
            "similarity_scores": Scores.from_records(
                pairs, ctx.output_dir / "similarity_scores.csv", directions={"similarity": "higher"}
            ),
        }

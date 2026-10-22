"""Similarity search method for finding similar molecules.

Computes molecular fingerprints and keeps database molecules whose Tanimoto
similarity to any query reaches a threshold.
"""

from collections import Counter
from typing import Any, Dict, List

from ...core.method import Method, MethodError, RunContext
from ...core.specs import ConfigField, EnvironmentSpec, PortSpec, ResourceSpec
from ...data import Molecules, Scores
from ..utils import rdkit_molecules, write_rdkit_sdf
from ._validation import compare_inventory, integer, number, occurrence_inventory, read_output, read_records


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

    def check_dependencies(self, inputs: Dict[str, Any], ctx: RunContext) -> List[str]:
        errors: List[str] = []
        for port in ("queries", "database"):
            records = read_records(inputs, port, errors)
            if port == "queries" and records is not None:
                try:
                    if not any(mol is not None for _, mol in rdkit_molecules(inputs[port])):
                        errors.append("No valid query molecules")
                except Exception as exc:
                    errors.append(f"Cannot parse query molecules: {exc}")
        return errors

    def validate_outputs(self, inputs: Dict[str, Any], outputs: Dict[str, Any], ctx: RunContext) -> List[str]:
        errors: List[str] = []
        source = {port: read_records(inputs, port, errors) for port in ("queries", "database")}
        molecules = read_output(outputs, "similar_molecules", "records", errors)
        rows = read_output(outputs, "similarity_scores", "rows", errors)
        expected = {port: occurrence_inventory(records or []) for port, records in source.items()}
        allowed = {port: {record["id"] for record in inventory} for port, inventory in expected.items()}
        pairs, values, molecule_order = [], [], []
        seen_molecules = set()
        for index, row in enumerate(rows or []):
            where = f"similarity_scores row {index + 1}"
            identifier, query = row.get("id"), row.get("query_id")
            if not isinstance(identifier, str) or identifier not in allowed["database"]:
                errors.append(f"{where}: unknown database id {identifier!r}")
            if not isinstance(query, str) or query not in allowed["queries"]:
                errors.append(f"{where}: unknown query id {query!r}")
            if isinstance(identifier, str) and isinstance(query, str):
                pairs.append((identifier, query))
                if identifier not in seen_molecules:
                    molecule_order.append(identifier)
                    seen_molecules.add(identifier)
            similarity = number(row.get("similarity"))
            if similarity is None or not self.config["similarity_threshold"] <= similarity <= 1:
                errors.append(f"{where}: similarity must be finite and between the configured threshold and 1")
            if similarity is not None:
                values.append(similarity)
        if len(set(pairs)) != len(pairs):
            errors.append("similarity_scores contains duplicate (id, query_id) pairs")
        if values != sorted(values, reverse=True):
            errors.append("similarity_scores must be sorted by descending similarity")
        if rows is not None and len(rows) > self.config["max_results"]:
            errors.append("similarity_scores exceeds max_results")
        if molecules is not None and rows is not None:
            compare_inventory([record.id for record in molecules], molecule_order, "similar_molecules inventory", errors)
            if [record.id for record in molecules] != molecule_order:
                errors.append("similar_molecules must follow their first score-row occurrence")
        for record in molecules or []:
            if not record.heavy_atoms or any(number(value) is None for point in record.heavy_atoms for value in point):
                errors.append(f"similar_molecules record {record.index + 1}: no usable heavy-atom coordinates")
        score_artifact = outputs.get("similarity_scores")
        metadata = getattr(score_artifact, "metadata", {}).get("search_inventory")
        if not isinstance(metadata, dict):
            errors.append("similarity_scores metadata needs a search_inventory accounting for every input")
            return errors
        if metadata.get("version") != 1:
            errors.append("Unsupported search_inventory version")
        returned = len(rows or [])
        candidates = integer(metadata.get("candidate_pairs"))
        if integer(metadata.get("returned_pairs")) != returned:
            errors.append("search_inventory.returned_pairs disagrees with score rows")
        if candidates is None or candidates < returned or integer(metadata.get("truncated_pairs")) != candidates - returned:
            errors.append("search_inventory candidate/returned/truncated counts do not form a partition")
        elif returned != min(candidates, self.config["max_results"]):
            errors.append("search_inventory does not account for max_results truncation")
        for port, column in (("database", 0), ("queries", 1)):
            inventory = metadata.get(port)
            if not isinstance(inventory, list):
                errors.append(f"search_inventory.{port} must account for every input")
                continue
            returned_counts = Counter(pair[column] for pair in pairs)
            identity_keys, matched_total = [], 0
            for index, item in enumerate(inventory):
                where = f"search_inventory.{port}[{index}]"
                if not isinstance(item, dict) or not all(isinstance(item.get(key), str) for key in ("id", "input_id")):
                    errors.append(f"{where}: malformed input identity")
                    continue
                identity_keys.append((item["id"], item["input_id"], integer(item.get("occurrence"), 1), integer(item.get("index"))))
                matched, count = integer(item.get("matching_pairs")), integer(item.get("returned_pairs"))
                if matched is None or count is None or matched < count:
                    errors.append(f"{where}: invalid matching/returned pair counts")
                    continue
                matched_total += matched
                if count != returned_counts[item["id"]]:
                    errors.append(f"{where}: returned_pairs disagrees with score rows")
                status = item.get("status")
                required = "matched" if count else ("truncated" if matched else "unmatched")
                if status == "invalid":
                    if matched or count or not isinstance(item.get("error"), str) or not item["error"]:
                        errors.append(f"{where}: invalid inputs need an error and zero pairs")
                elif status != required:
                    errors.append(f"{where}: status disagrees with pair counts")
            if source[port] is not None:
                compare_inventory(identity_keys, [(item["id"], item["input_id"], item["occurrence"], item["index"]) for item in expected[port]],
                                  f"search_inventory.{port} input inventory", errors)
            if candidates is not None and matched_total != candidates:
                errors.append(f"search_inventory.{port} matching_pairs sum disagrees with candidate_pairs")
        molecule_metadata = getattr(outputs.get("similar_molecules"), "metadata", {}).get("search_inventory")
        if outputs.get("similar_molecules") is not None and molecule_metadata != metadata:
            errors.append("similar_molecules and similarity_scores search inventories disagree")
        return errors

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

        query_records = rdkit_molecules(inputs["queries"])
        database_records = rdkit_molecules(inputs["database"])
        inventory = {
            "version": 1,
            "queries": occurrence_inventory([record for record, _ in query_records]),
            "database": occurrence_inventory([record for record, _ in database_records]),
        }
        queries = []
        for item, (_, mol) in zip(inventory["queries"], query_records):
            item.update(matching_pairs=0, returned_pairs=0, status="unmatched")
            if mol is None:
                item.update(status="invalid", error="Unparsable query molecule")
                continue
            queries.append((item, self._fingerprint(mol)))
        if not queries:
            raise MethodError("No valid query molecules")
        pairs = []
        molecules = {}
        for item, (record, mol) in zip(inventory["database"], database_records):
            item.update(matching_pairs=0, returned_pairs=0, status="unmatched")
            if mol is None:
                item.update(status="invalid", error="Unparsable database molecule")
                continue
            fingerprint = self._fingerprint(mol)
            for query, query_fp in queries:
                similarity = DataStructs.TanimotoSimilarity(query_fp, fingerprint)
                if similarity >= self.config["similarity_threshold"]:
                    pairs.append({"id": item["id"], "query_id": query["id"], "similarity": similarity})
                    item["matching_pairs"] += 1
                    query["matching_pairs"] += 1
                    # Duplicate input names represent distinct molecules throughout the search.
                    if item["id"] != record.id:
                        mol.SetProp("_Name", item["id"])
                    molecules[item["id"]] = mol
        pairs.sort(key=lambda row: row["similarity"], reverse=True)
        inventory["candidate_pairs"] = len(pairs)
        pairs = pairs[: self.config["max_results"]]
        inventory["returned_pairs"] = len(pairs)
        inventory["truncated_pairs"] = inventory["candidate_pairs"] - len(pairs)
        for port, key in (("queries", "query_id"), ("database", "id")):
            counts = Counter(row[key] for row in pairs)
            for item in inventory[port]:
                item["returned_pairs"] = counts[item["id"]]
                if item["status"] != "invalid":
                    item["status"] = "matched" if item["returned_pairs"] else ("truncated" if item["matching_pairs"] else "unmatched")

        kept = []
        for row in pairs:  # Best similarity first, each molecule once.
            mol = molecules.pop(row["id"], None)
            if mol is not None:
                mol.SetProp("query_id", row["query_id"])
                mol.SetProp("similarity", f"{row['similarity']:.3f}")
                kept.append(mol)
        return {
            "similar_molecules": Molecules.from_file(write_rdkit_sdf(kept, ctx.output_dir / "similar.sdf"), metadata={"search_inventory": inventory}),
            "similarity_scores": Scores.from_records(
                pairs, ctx.output_dir / "similarity_scores.csv", directions={"similarity": "higher"},
                metadata={"search_inventory": inventory},
            ),
        }

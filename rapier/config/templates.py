"""
Template pipeline configuration generators.

Provides helper functions to quickly create common pipeline configurations.
"""

from typing import List, Dict, Any, Optional
from .pipeline_config import PipelineConfig
from .method_config import MethodConfig, ConnectionConfig, PoolingRuleConfig


def create_simple_pipeline(
    name: str,
    method_types: List[str],
    description: Optional[str] = None
) -> PipelineConfig:
    """
    Create a simple serial pipeline: A -> B -> C -> ...

    Args:
        name: Pipeline name
        method_types: List of method types in order
        description: Optional pipeline description

    Returns:
        PipelineConfig instance

    Example:
        >>> config = create_simple_pipeline(
        ...     "screening_pipeline",
        ...     ["filtering", "docking", "scoring"]
        ... )
    """
    # Create methods
    methods = []
    for i, method_type in enumerate(method_types):
        method_id = f"step_{i+1}"
        methods.append(MethodConfig(
            id=method_id,
            type=method_type,
            description=f"Step {i+1}: {method_type}"
        ))

    # Create serial connections
    connections = []
    for i in range(len(methods) - 1):
        connections.append(ConnectionConfig(
            from_methods=methods[i].id,
            to_method=methods[i+1].id,
            connection_type="serial"
        ))

    return PipelineConfig(
        name=name,
        description=description or f"Simple serial pipeline with {len(methods)} steps",
        methods=methods,
        connections=connections
    )


def create_parallel_pipeline(
    name: str,
    initial_method: str,
    parallel_methods: List[str],
    final_method: Optional[str] = None,
    description: Optional[str] = None
) -> PipelineConfig:
    """
    Create a pipeline with parallel branches: A -> [B, C, D] -> E

    Args:
        name: Pipeline name
        initial_method: Type of the initial method
        parallel_methods: List of method types to run in parallel
        final_method: Optional final method type
        description: Optional pipeline description

    Returns:
        PipelineConfig instance

    Example:
        >>> config = create_parallel_pipeline(
        ...     "multi_docking",
        ...     initial_method="preparation",
        ...     parallel_methods=["vina_dock", "autodock", "glide"],
        ...     final_method="consensus"
        ... )
    """
    methods = []
    connections = []

    # Create initial method
    init_method = MethodConfig(
        id="initial",
        type=initial_method,
        description=f"Initial step: {initial_method}"
    )
    methods.append(init_method)

    # Create parallel methods
    parallel_ids = []
    for i, method_type in enumerate(parallel_methods):
        method_id = f"parallel_{i+1}"
        parallel_ids.append(method_id)
        methods.append(MethodConfig(
            id=method_id,
            type=method_type,
            description=f"Parallel branch {i+1}: {method_type}"
        ))

        # Connect initial to each parallel method
        connections.append(ConnectionConfig(
            from_methods=init_method.id,
            to_method=method_id,
            connection_type="parallel"
        ))

    # Create final method if specified
    if final_method:
        final_id = "final"
        methods.append(MethodConfig(
            id=final_id,
            type=final_method,
            description=f"Final step: {final_method}"
        ))

        # Connect all parallel methods to final
        connections.append(ConnectionConfig(
            from_methods=parallel_ids,
            to_method=final_id,
            connection_type="parallel"
        ))

    return PipelineConfig(
        name=name,
        description=description or f"Parallel pipeline with {len(parallel_methods)} branches",
        methods=methods,
        connections=connections
    )


def create_pooling_pipeline(
    name: str,
    initial_methods: List[str],
    pooling_method: str,
    pooling_strategy: str = "all",
    final_method: Optional[str] = None,
    metric: Optional[str] = None,
    description: Optional[str] = None
) -> PipelineConfig:
    """
    Create a pipeline with pooling: [A, B, C] -> Pool -> D

    Args:
        name: Pipeline name
        initial_methods: List of method types to run in parallel
        pooling_method: Type of the pooling method
        pooling_strategy: Pooling strategy ('any', 'all', 'majority', 'best', 'first')
        final_method: Optional final method type after pooling
        metric: Metric name for 'best' strategy
        description: Optional pipeline description

    Returns:
        PipelineConfig instance

    Example:
        >>> config = create_pooling_pipeline(
        ...     "consensus_docking",
        ...     initial_methods=["vina_dock", "autodock"],
        ...     pooling_method="consensus_filter",
        ...     pooling_strategy="best",
        ...     metric="docking_score"
        ... )
    """
    methods = []
    connections = []

    # Create initial parallel methods
    initial_ids = []
    for i, method_type in enumerate(initial_methods):
        method_id = f"initial_{i+1}"
        initial_ids.append(method_id)
        methods.append(MethodConfig(
            id=method_id,
            type=method_type,
            description=f"Initial method {i+1}: {method_type}"
        ))

    # Create pooling method
    pool_id = "pooling"
    methods.append(MethodConfig(
        id=pool_id,
        type=pooling_method,
        description=f"Pooling step: {pooling_method}"
    ))

    # Connect initial methods to pooling
    connections.append(ConnectionConfig(
        from_methods=initial_ids,
        to_method=pool_id,
        connection_type="parallel"
    ))

    # Create pooling rule
    pooling_rules = {
        pool_id: PoolingRuleConfig(
            strategy=pooling_strategy,
            metric=metric if pooling_strategy == "best" else None
        )
    }

    # Create final method if specified
    if final_method:
        final_id = "final"
        methods.append(MethodConfig(
            id=final_id,
            type=final_method,
            description=f"Final step: {final_method}"
        ))

        connections.append(ConnectionConfig(
            from_methods=pool_id,
            to_method=final_id,
            connection_type="serial"
        ))

    return PipelineConfig(
        name=name,
        description=description or f"Pooling pipeline with {len(initial_methods)} inputs",
        methods=methods,
        connections=connections,
        pooling_rules=pooling_rules
    )


def create_diamond_pipeline(
    name: str,
    preparation_method: str,
    parallel_methods: List[str],
    pooling_method: str,
    final_method: str,
    pooling_strategy: str = "all",
    metric: Optional[str] = None,
    description: Optional[str] = None
) -> PipelineConfig:
    """
    Create a diamond-shaped pipeline: A -> [B, C, D] -> Pool -> E

    Args:
        name: Pipeline name
        preparation_method: Type of initial preparation method
        parallel_methods: List of method types to run in parallel
        pooling_method: Type of the pooling method
        final_method: Type of the final method
        pooling_strategy: Pooling strategy ('any', 'all', 'majority', 'best', 'first')
        metric: Metric name for 'best' strategy
        description: Optional pipeline description

    Returns:
        PipelineConfig instance

    Example:
        >>> config = create_diamond_pipeline(
        ...     "full_screening",
        ...     preparation_method="ligand_prep",
        ...     parallel_methods=["vina", "autodock", "smina"],
        ...     pooling_method="consensus",
        ...     final_method="scoring",
        ...     pooling_strategy="majority"
        ... )
    """
    methods = []
    connections = []

    # Create preparation method
    prep_id = "preparation"
    methods.append(MethodConfig(
        id=prep_id,
        type=preparation_method,
        description=f"Preparation: {preparation_method}"
    ))

    # Create parallel methods
    parallel_ids = []
    for i, method_type in enumerate(parallel_methods):
        method_id = f"parallel_{i+1}"
        parallel_ids.append(method_id)
        methods.append(MethodConfig(
            id=method_id,
            type=method_type,
            description=f"Parallel method {i+1}: {method_type}"
        ))

        # Connect prep to parallel
        connections.append(ConnectionConfig(
            from_methods=prep_id,
            to_method=method_id,
            connection_type="parallel"
        ))

    # Create pooling method
    pool_id = "pooling"
    methods.append(MethodConfig(
        id=pool_id,
        type=pooling_method,
        description=f"Pooling: {pooling_method}"
    ))

    # Connect parallel to pooling
    connections.append(ConnectionConfig(
        from_methods=parallel_ids,
        to_method=pool_id,
        connection_type="parallel"
    ))

    # Create pooling rule
    pooling_rules = {
        pool_id: PoolingRuleConfig(
            strategy=pooling_strategy,
            metric=metric if pooling_strategy == "best" else None
        )
    }

    # Create final method
    final_id = "final"
    methods.append(MethodConfig(
        id=final_id,
        type=final_method,
        description=f"Final: {final_method}"
    ))

    # Connect pooling to final
    connections.append(ConnectionConfig(
        from_methods=pool_id,
        to_method=final_id,
        connection_type="serial"
    ))

    return PipelineConfig(
        name=name,
        description=description or f"Diamond pipeline with {len(parallel_methods)} parallel branches",
        methods=methods,
        connections=connections,
        pooling_rules=pooling_rules
    )

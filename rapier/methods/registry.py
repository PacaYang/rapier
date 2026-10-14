"""Method registry for discovering and managing available methods.

The registry provides a centralized way to register, discover, and instantiate
screening methods. It supports auto-discovery of methods in the methods/ directory
and allows users to register custom methods.
"""

from typing import Dict, List, Type, Optional, Any
from pathlib import Path
import importlib
import inspect
import pkgutil

from ..core.method import Method


class MethodRegistry:
    """Singleton registry for all available screening methods.

    The registry maintains a mapping of method names to method classes,
    supports auto-discovery of built-in methods, and allows registration
    of custom methods.

    Example:
        >>> registry = MethodRegistry()
        >>> registry.register('my_method', MyMethodClass)
        >>> method_class = registry.get('my_method')
        >>> method = method_class(name='test', version='1.0')
    """

    _instance = None
    _initialized = False

    def __new__(cls):
        """Implement singleton pattern."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        """Initialize the registry (only once due to singleton)."""
        if not MethodRegistry._initialized:
            self._methods: Dict[str, Type[Method]] = {}
            self._method_info: Dict[str, Dict[str, Any]] = {}
            MethodRegistry._initialized = True

    def register(
        self,
        name: str,
        method_class: Type[Method],
        metadata: Optional[Dict[str, Any]] = None
    ) -> None:
        """Register a method with the registry.

        Args:
            name: Unique identifier for the method
            method_class: The method class (must inherit from Method)
            metadata: Optional metadata about the method

        Raises:
            ValueError: If name is already registered or method_class is invalid
        """
        if name in self._methods:
            raise ValueError(f"Method '{name}' is already registered")

        if not issubclass(method_class, Method):
            raise ValueError(
                f"Method class must inherit from Method, got {method_class}"
            )

        self._methods[name] = method_class
        self._method_info[name] = metadata or {}

        # Extract basic info from the class if not provided
        if 'description' not in self._method_info[name]:
            self._method_info[name]['description'] = (
                method_class.__doc__.split('\n')[0] if method_class.__doc__ else ''
            )

    def unregister(self, name: str) -> None:
        """Remove a method from the registry.

        Args:
            name: Name of the method to remove

        Raises:
            KeyError: If the method is not registered
        """
        if name not in self._methods:
            raise KeyError(f"Method '{name}' is not registered")

        del self._methods[name]
        del self._method_info[name]

    def get(self, name: str) -> Type[Method]:
        """Get a method class by name.

        Args:
            name: Name of the method

        Returns:
            The method class

        Raises:
            KeyError: If the method is not registered
        """
        if name not in self._methods:
            raise KeyError(
                f"Method '{name}' not found. Available methods: {self.list_available()}"
            )
        return self._methods[name]

    def has(self, name: str) -> bool:
        """Check if a method is registered.

        Args:
            name: Name of the method

        Returns:
            True if the method is registered
        """
        return name in self._methods

    def list_available(self) -> List[str]:
        """Get a list of all registered method names.

        Returns:
            Sorted list of method names
        """
        return sorted(self._methods.keys())

    def get_info(self, name: str) -> Dict[str, Any]:
        """Get metadata about a method.

        Args:
            name: Name of the method

        Returns:
            Dict containing method metadata

        Raises:
            KeyError: If the method is not registered
        """
        if name not in self._method_info:
            raise KeyError(f"Method '{name}' not found")

        # Merge class info with stored metadata
        method_class = self._methods[name]
        info = self._method_info[name].copy()

        # Add class-level information
        info['class'] = method_class.__name__
        info['module'] = method_class.__module__

        return info

    def list_info(self) -> Dict[str, Dict[str, Any]]:
        """Get metadata for all registered methods.

        Returns:
            Dict mapping method names to their metadata
        """
        return {name: self.get_info(name) for name in self.list_available()}

    def auto_discover(self, package_path: Optional[Path] = None) -> int:
        """Automatically discover and register methods in the methods package.

        This walks through the rapier.methods package and its subpackages,
        looking for classes that inherit from Method and have a
        `register_name` class attribute.

        Args:
            package_path: Optional path to the methods package directory.
                         If None, uses rapier/methods/

        Returns:
            Number of methods discovered and registered

        Example:
            Method classes can opt into auto-discovery:

            class MyMethod(Method):
                register_name = "my_method"  # Will be auto-registered
        """
        discovered_count = 0

        # Determine package path
        if package_path is None:
            # Get the path to rapier/methods/
            current_file = Path(__file__)
            package_path = current_file.parent

        # Import the methods package
        try:
            methods_package = importlib.import_module('rapier.methods')
        except ImportError:
            return 0

        # Walk through all modules in the package
        for importer, modname, ispkg in pkgutil.walk_packages(
            path=[str(package_path)],
            prefix='rapier.methods.'
        ):
            try:
                module = importlib.import_module(modname)

                # Look for Method subclasses with register_name
                for name, obj in inspect.getmembers(module, inspect.isclass):
                    # Skip Method itself
                    if obj is Method:
                        continue

                    # Check if it's a Method subclass
                    if issubclass(obj, Method):
                        # Check for register_name attribute
                        register_name = getattr(obj, 'register_name', None)
                        if register_name and not self.has(register_name):
                            self.register(register_name, obj)
                            discovered_count += 1

            except Exception as e:
                # Log but don't fail on import errors
                print(f"Warning: Failed to import {modname}: {e}")
                continue

        return discovered_count

    def clear(self) -> None:
        """Clear all registered methods.

        Warning: This removes all methods including built-ins.
        Mainly useful for testing.
        """
        self._methods.clear()
        self._method_info.clear()


# Global registry instance
_global_registry = MethodRegistry()


def get_registry() -> MethodRegistry:
    """Get the global method registry instance.

    Returns:
        The singleton MethodRegistry instance
    """
    return _global_registry

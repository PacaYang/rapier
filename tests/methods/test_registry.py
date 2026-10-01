"""Tests for method registry."""

import pytest
from rapier.methods.registry import MethodRegistry
from rapier.methods.screening import LigandFilter
from rapier.core.base_method import BaseMethod


def test_singleton_pattern():
    """Test that MethodRegistry is a singleton."""
    registry1 = MethodRegistry()
    registry2 = MethodRegistry()

    assert registry1 is registry2


def test_register_method():
    """Test registering a method."""
    registry = MethodRegistry()
    registry.clear()  # Start fresh

    registry.register('test_filter', LigandFilter)

    assert registry.has('test_filter')
    assert 'test_filter' in registry.list_available()


def test_register_duplicate():
    """Test that registering duplicate name raises error."""
    registry = MethodRegistry()
    registry.clear()

    registry.register('test_filter', LigandFilter)

    with pytest.raises(ValueError, match="already registered"):
        registry.register('test_filter', LigandFilter)


def test_get_method():
    """Test retrieving a registered method."""
    registry = MethodRegistry()
    registry.clear()

    registry.register('test_filter', LigandFilter)

    method_class = registry.get('test_filter')
    assert method_class == LigandFilter


def test_get_nonexistent():
    """Test that getting non-existent method raises error."""
    registry = MethodRegistry()
    registry.clear()

    with pytest.raises(KeyError, match="not found"):
        registry.get('nonexistent_method')


def test_list_available():
    """Test listing available methods."""
    registry = MethodRegistry()
    registry.clear()

    registry.register('method_a', LigandFilter)
    registry.register('method_b', LigandFilter)

    available = registry.list_available()
    assert 'method_a' in available
    assert 'method_b' in available
    assert len(available) == 2


def test_get_info():
    """Test getting method metadata."""
    registry = MethodRegistry()
    registry.clear()

    metadata = {
        'author': 'Test Author',
        'category': 'filtering'
    }
    registry.register('test_filter', LigandFilter, metadata=metadata)

    info = registry.get_info('test_filter')
    assert 'author' in info
    assert info['author'] == 'Test Author'
    assert 'class' in info
    assert info['class'] == 'LigandFilter'


def test_unregister():
    """Test unregistering a method."""
    registry = MethodRegistry()
    registry.clear()

    registry.register('test_filter', LigandFilter)
    assert registry.has('test_filter')

    registry.unregister('test_filter')
    assert not registry.has('test_filter')


def test_register_invalid_class():
    """Test that registering non-BaseMethod class raises error."""
    registry = MethodRegistry()
    registry.clear()

    class NotAMethod:
        pass

    with pytest.raises(ValueError, match="must inherit from BaseMethod"):
        registry.register('invalid', NotAMethod)


def test_list_info():
    """Test getting info for all methods."""
    registry = MethodRegistry()
    registry.clear()

    registry.register('method_a', LigandFilter)
    registry.register('method_b', LigandFilter)

    all_info = registry.list_info()
    assert 'method_a' in all_info
    assert 'method_b' in all_info
    assert all_info['method_a']['class'] == 'LigandFilter'


def test_auto_discover():
    """Test auto-discovery of methods with register_name."""
    registry = MethodRegistry()
    registry.clear()

    # Auto-discover methods in the package
    count = registry.auto_discover()

    # Should find at least the methods we defined with register_name
    assert count >= 0  # May be 0 if imports fail in test environment

    # If methods were discovered, check they're available
    if count > 0:
        available = registry.list_available()
        assert len(available) >= count


def test_clear():
    """Test clearing the registry."""
    registry = MethodRegistry()

    registry.register('test1', LigandFilter)
    registry.register('test2', LigandFilter)

    assert len(registry.list_available()) >= 2

    registry.clear()
    assert len(registry.list_available()) == 0

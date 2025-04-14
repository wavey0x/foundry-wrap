"""
Error handling utilities for safesmith.

This module provides standardized error handling across the codebase,
with custom exception types and utility functions for consistent error reporting.
"""

from typing import Optional, Tuple, Any, TypeVar, Callable, Dict, Union
import functools
from functools import wraps
import logging
import traceback
from rich.console import Console

# Create a console instance for rich output
console = Console()

# Setup logger
logger = logging.getLogger("safesmith")

# Global flag to track if an error has been displayed
_ERROR_DISPLAYED = False

# Type variable for return type
T = TypeVar('T')
R = TypeVar('R')

# Base exception class for all safesmith errors
class SafesmithError(Exception):
    """Base exception class for all safesmith errors."""
    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}

    def __str__(self) -> str:
        if self.details:
            details_str = ", ".join(f"{k}={v}" for k, v in self.details.items())
            return f"{self.message} ({details_str})"
        return self.message

# Specific exception types
class ConfigError(SafesmithError):
    """Error related to configuration settings."""
    pass

class InterfaceError(SafesmithError):
    """Error related to interface management."""
    pass

class ScriptError(SafesmithError):
    """Error related to script parsing or execution."""
    pass

class WalletError(SafesmithError):
    """Error related to wallet operations."""
    pass

class NetworkError(SafesmithError):
    """Error related to network operations or RPC calls."""
    pass

class SafeError(SafesmithError):
    """Error related to Gnosis Safe operations."""
    pass

class ValidationError(SafesmithError):
    """Error related to data validation."""
    pass

# Override console.print to suppress duplicate error messages
_original_console_print = console.print
def _filtered_console_print(*args, **kwargs):
    global _ERROR_DISPLAYED
    
    # Check if this is an error message
    text = ' '.join(str(arg) for arg in args if isinstance(arg, str))
    is_error = any(error_text in text for error_text in ['Error in', 'Error:', 'Exception:'])
    
    # Only print if not an error or if it's the first error
    if not is_error or not _ERROR_DISPLAYED:
        if is_error:
            _ERROR_DISPLAYED = True
        _original_console_print(*args, **kwargs)

# Monkey patch console.print
console.print = _filtered_console_print

# Helper function to standardize error handling
def handle_errors(error_type=None, log_error=True):
    """
    Decorator to handle errors in a consistent way.
    
    Args:
        error_type: If specified, exceptions will be converted to this type.
                   If None, exceptions will be re-raised as-is.
        log_error: Whether to log errors.
    
    Returns:
        The decorator function.
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            try:
                # Simply return the function's result without wrapping in a tuple
                return func(*args, **kwargs)
            except Exception as e:
                # Log the error if requested
                if log_error:
                    logger.error(f"Error in {func.__name__}: {str(e)}", exc_info=True)
                
                # NO display functionality - all display happens at CLI level
                
                if error_type:
                    # If the error is already of the target type or a subclass, don't re-wrap
                    if isinstance(e, error_type):
                        # Just re-raise the original error
                        raise
                    
                    # Convert to the target error type
                    # Include the original exception type
                    error_data = {
                        "exception_type": type(e).__name__
                    }
                    
                    # Add any additional error data if present
                    if hasattr(e, "details") and isinstance(e.details, dict):
                        error_data.update(e.details)
                    
                    # Create and raise the converted error
                    raise error_type(str(e), error_data)
                else:
                    # Re-raise the original exception
                    raise
        return wrapper
    return decorator

# Utility function to convert return tuples to results/exceptions
def result_or_raise(value_or_tuple, error_type=None):
    """
    This function is a compatibility layer for the old handle_errors decorator.
    It was used to handle return values from functions that returned (success, value, error) tuples.
    
    It handles:
    1. Direct values (just returns them)
    2. Old-style tuples like (True, value, None) or (False, None, error)
    
    Args:
        value_or_tuple: Either a direct value or a tuple from a handle_errors decorated function
        error_type: The error type to raise if the operation failed
    
    Returns:
        The value if successful, otherwise raises an exception
    """
    # If it's not a tuple or not in the expected format, just return it directly
    if not isinstance(value_or_tuple, tuple) or len(value_or_tuple) != 3:
        return value_or_tuple
    
    # Check if it looks like our old (success, value, error) tuple
    success, value, error = value_or_tuple
    
    if not isinstance(success, bool):
        # If the first element isn't a boolean, it's probably not our special tuple
        return value_or_tuple
    
    # Handle old-style tuple
    if success:
        return value
    else:
        if error_type:
            if isinstance(error, dict):
                raise error_type(str(error.get('message', 'Unknown error')), error)
            else:
                raise error_type(str(error))
        else:
            if isinstance(error, Exception):
                raise error
            else:
                raise Exception(str(error)) 
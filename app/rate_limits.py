"""
Rate limit configuration for API endpoints.

Prevents system abuse by limiting requests per user.
"""

# Rate limit strings (format: "number/time_unit")
# Time units: second, minute, hour, day

# Authentication endpoints
RATE_LIMIT_AUTH_REGISTER = "10/hour"  # Max 10 registrations per hour per IP
RATE_LIMIT_AUTH_LOGIN = "20/hour"     # Max 20 login attempts per hour

# Project endpoints
RATE_LIMIT_PROJECT_CREATE = "50/hour"  # Max 50 projects per hour per user
RATE_LIMIT_PROJECT_LIST = "100/minute"  # Max 100 list requests per minute

# Document endpoints
RATE_LIMIT_DOCUMENT_UPLOAD = "50/hour"  # Max 50 PDF uploads per hour per user
RATE_LIMIT_DOCUMENT_LIST = "100/minute"  # Max 100 list requests per minute

# Form endpoints
RATE_LIMIT_FORM_CREATE = "10/minute"  # Max 10 forms per minute per user (expensive!)
RATE_LIMIT_FORM_LIST = "100/minute"    # Max 100 list requests per minute

# Extraction endpoints
RATE_LIMIT_EXTRACTION_CREATE = "20/hour"  # Max 20 extraction jobs per hour per user
RATE_LIMIT_EXTRACTION_LIST = "100/minute"  # Max 100 list requests per minute

# Results endpoints
RATE_LIMIT_RESULTS_EXPORT = "50/hour"  # Max 50 exports per hour per user
RATE_LIMIT_RESULTS_LIST = "100/minute"  # Max 100 list requests per minute

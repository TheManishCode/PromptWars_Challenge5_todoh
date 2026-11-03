"""Quote documents (architecture L7): a vendor's quote file becomes lines the user can confirm. Nothing here records a price as fact.

``sandbox`` converts an untrusted file to markdown in a separate, resource-limited process; ``quotes`` reads priced lines out of that markdown and keeps only
amounts that are written in it. The converter engines are pluggable: text, CSV and HTML are built in, PDF, Word, Excel and images need Docling if it is installed.
"""

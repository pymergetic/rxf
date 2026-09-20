# Metal integration

Metal is an optional runtime and capability environment for RXF. It is not part of the RXF format, object model, validator, compiler contract, or native envelope specification.

An integration may bind explicit RXF capability requirements to Metal services, expose RXF inspection through Metal transports, or load an RXF image in a Metal-supported host, browser, MicroPython, or firmware environment. Those bindings must remain visible as imports, provider metadata, target constraints, and runtime policy.

Metal concepts such as cards, seats, fills, parks, and its allocator implementation are local to Metal. They do not become RXF object kinds or normative terminology. RXF must remain usable with independent providers and without Metal installed.

See the separate [Metal repository](https://github.com/pymergetic/metal) for its architecture and documentation.

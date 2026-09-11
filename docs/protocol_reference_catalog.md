# Industrial protocol reference catalog

Research date: 2026-09-11.

This catalog is the reference base for implementing protocol state machines and
traffic generators in the agentic PLC honeypot. It intentionally separates
official specifications from secondary explanations and open-source code.

## Source tiers

1. Official standards, RFCs, or specification portals.
2. Official conformance, profile, errata, or developer-guidance material.
3. Mature open-source protocol stacks and Wireshark dissectors.
4. Secondary explainers. These are useful for orientation, but not enough for
   implementing state-machine rules.

For copyrighted or licensed standards, do not copy specification text into the
repo unless we have the right to do so. Keep URLs and implementation notes here;
derive code from open specifications, permitted documentation, our own packet
observations, and compatible open-source implementations.

## Modbus TCP

Primary sources:

- Modbus Organization specifications and implementation guides:
  https://www.modbus.org/
- Modbus Application Protocol Specification V1.1b3:
  https://www.modbus.org/docs/Modbus_Application_Protocol_V1_1b3.pdf
- Modbus Messaging on TCP/IP Implementation Guide V1.0b:
  https://www.modbus.org/docs/Modbus_Messaging_Implementation_Guide_V1_0b.pdf

Implementation references:

- libmodbus: https://libmodbus.org/
- pymodbus: https://github.com/pymodbus-dev/pymodbus
- Wireshark source tree: https://gitlab.com/wireshark/wireshark

State-machine implications:

- Modbus TCP is mostly request/response and session-light.
- The important state is per connection and per transaction: MBAP transaction
  id, protocol id, length, unit id, request function, exception response shape,
  and read-after-write consistency.
- This is the best first protocol for validating our generic framework because
  protocol state is modest while physical-process consistency can be tested
  deeply.

## Siemens S7 / ISO-on-TCP

Primary sources:

- RFC 1006, ISO Transport Service on top of TCP:
  https://www.rfc-editor.org/rfc/rfc1006.txt
- RFC 905, ISO Transport Protocol Specification / ISO DP 8073:
  https://www.rfc-editor.org/rfc/rfc905.txt
- Siemens S7 communication documentation portal:
  https://docs.tia.siemens.cloud/
- Siemens Industry Support example for S7 communication:
  https://support.industry.siemens.com/

Implementation and dissector references:

- Wireshark S7comm dissector in the Wireshark source tree:
  https://gitlab.com/wireshark/wireshark
- Wireshark S7comm wiki:
  https://wiki.wireshark.org/S7comm
- python-snap7 documentation:
  https://python-snap7.readthedocs.io/
- python-snap7 source:
  https://github.com/gijzelaerr/python-snap7

State-machine implications:

- Publicly available Siemens wire-level material is incomplete because S7comm is
  proprietary. Treat Wireshark and open-source stacks as implementation
  references, not official specifications.
- The state machine should model TCP connection, TPKT, COTP connection request
  and confirm, S7 setup communication, PDU reference tracking, then read/write
  jobs.
- For S7-like support, we should start with a constrained emulation profile
  rather than claiming broad Siemens compatibility.

## OPC UA

Primary sources:

- OPC Foundation online reference:
  https://reference.opcfoundation.org/
- Part 4, Services:
  https://reference.opcfoundation.org/Core/Part4/
- Part 6, Mappings:
  https://reference.opcfoundation.org/Core/Part6/
- Part 16, State Machines:
  https://reference.opcfoundation.org/Core/Part16/
- Part 18, Role-Based Security:
  https://reference.opcfoundation.org/Core/Part18/
- OPC UA for PLCs based on IEC 61131-3:
  https://reference.opcfoundation.org/PLCopen/

Implementation references:

- OPC Foundation UA .NET Standard stack:
  https://github.com/OPCFoundation/UA-.NETStandard
- open62541:
  https://www.open62541.org/
- Eclipse Milo:
  https://projects.eclipse.org/projects/iot.milo
- Wireshark source tree:
  https://gitlab.com/wireshark/wireshark

State-machine implications:

- OPC UA needs real protocol gating: endpoint discovery, SecureChannel,
  CreateSession, ActivateSession, service calls, subscriptions, and role/security
  constraints.
- This protocol is a strong candidate for evaluating whether the agent respects
  authentication/session state before generating semantic replies.

## DNP3 / IEEE 1815

Primary sources:

- DNP Users Group:
  https://www.dnp.org/
- IEEE 1815 standard landing page:
  https://standards.ieee.org/
- DNP public documents and device-profile material:
  https://www.dnp.org/

Implementation and dissector references:

- opendnp3:
  https://github.com/dnp3/opendnp3
- Wireshark source tree:
  https://gitlab.com/wireshark/wireshark
- Wireshark DNP3 display-filter reference:
  https://www.wireshark.org/docs/dfref/d/dnp3.html

State-machine implications:

- DNP3 requires link/application-layer sequencing, confirms, unsolicited
  response handling, internal indication bits, class scans, and select-before-
  operate semantics.
- For honeypot consistency, DNP3 is important because many control operations
  should be illegal unless a prior select or authentication step occurred.

## IEC 60870-5-104

Primary sources:

- IEC 60870-5-104:2006 official landing page:
  https://webstore.iec.ch/en/publication/3746
- IEC 60870-5-104 amendment:
  https://webstore.iec.ch/en/publication/25054
- IEC TS 60870-5-604 conformance test cases:
  https://webstore.iec.ch/en/publication/25058
- IEC TS 60870-5-7 secure communication extension:
  https://webstore.iec.ch/en/publication/87773

Implementation and dissector references:

- lib60870:
  https://github.com/mz-automation/lib60870
- lib60870 user guide:
  https://github.com/mz-automation/lib60870/blob/master/user_guide.adoc
- Wireshark source tree:
  https://gitlab.com/wireshark/wireshark
- Wireshark IEC 60870 ASDU display-filter reference:
  https://www.wireshark.org/docs/dfref/i/iec60870_asdu.html

State-machine implications:

- IEC-104 state is connection-oriented and sequence-sensitive: STARTDT, STOPDT,
  TESTFR, I/S/U frames, send/receive sequence numbers, ASDU type id, cause of
  transmission, common address, and information object address.
- Control commands should be checked against select/execute and cause-of-
  transmission expectations before mutating the process backend.

## EtherNet/IP and CIP

Primary sources:

- ODVA technologies and standards:
  https://www.odva.org/
- ODVA EtherNet/IP:
  https://www.odva.org/technology-standards/key-technologies/ethernet-ip/
- ODVA developer guide for EtherNet/IP:
  https://www.odva.org/wp-content/uploads/2020/05/PUB00213R0_EtherNetIP_Developers_Guide.pdf

Implementation and dissector references:

- OpENer EtherNet/IP stack:
  https://github.com/EIPStackGroup/OpENer
- EIPScanner:
  https://github.com/nimbuscontrols/EIPScanner
- Wireshark source tree:
  https://gitlab.com/wireshark/wireshark
- Wireshark EtherNet/IP display-filter reference:
  https://www.wireshark.org/docs/dfref/e/enip.html

State-machine implications:

- Full ODVA specifications are licensed. Use the developer guide, conformance
  concepts, open-source stacks, and packet captures to define a bounded
  emulation profile.
- State should include encapsulation RegisterSession/UnRegisterSession, session
  handles, SendRRData vs SendUnitData, CIP service/class/instance/attribute, and
  connection management such as Forward Open/Close.

## BACnet/IP

Primary sources:

- BACnet Committee / ASHRAE SSPC 135:
  https://bacnet.org/
- ASHRAE BACnet bookstore and standard page:
  https://www.ashrae.org/technical-resources/bookstore/bacnet
- BACnet addenda and standard updates:
  https://bacnet.org/addenda/
- BACnet Testing Laboratories:
  https://btl.org/

Implementation and dissector references:

- BACnet Stack:
  https://github.com/bacnet-stack/bacnet-stack
- BACpypes3:
  https://bacpypes3.readthedocs.io/
- Wireshark BACnet wiki:
  https://wiki.wireshark.org/Protocols/bacnet
- Wireshark source tree:
  https://gitlab.com/wireshark/wireshark

State-machine implications:

- BACnet/IP requires BVLC handling, Who-Is/I-Am discovery state, device/object
  identity, confirmed-service invoke ids, segmentation windows, errors/rejects,
  and optionally BACnet/SC secure-channel behavior.
- It is useful for evaluating semantic consistency because properties, units,
  object types, schedules, alarms, and writable points need to match the physical
  process scenario.

## Design consequences for our honeypot

The protocol state-machine layer should be distinct from the existing attacker
interaction tracker:

```text
ProtocolStateMachine: is this protocol transition legal now?
ProtocolIntentTracker: what is the attacker trying to do over time?
PhysicalProcessContext: which process variable does this operation affect?
```

The traffic generator should include:

- valid happy-path sessions;
- invalid transition tests, such as read/write before handshake or session setup;
- sequence-number and transaction-id perturbations;
- authentication or role-gated operation attempts where the protocol supports
  them;
- select-before-operate control flows;
- write-then-read-back flows;
- multi-session and multi-actor interleavings;
- protocol-valid but physically suspicious writes;
- LLM-generated responses compared against rule-based fallback.

For implementation order, keep Modbus as the first runnable baseline, then add a
generic `ProtocolStateMachine` interface and use S7/ISO-on-TCP or IEC-104 as the
first genuinely stateful protocol profile.

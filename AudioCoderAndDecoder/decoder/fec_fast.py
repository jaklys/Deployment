"""
Reed-Solomon Forward Error Correction over GF(256).

ZERO external dependencies - stdlib only.

Implements RS(255,223) by default:
- 223 message bytes + 32 parity bytes = 255 byte codeword
- Corrects up to 16 symbol (byte) errors per codeword
- Primitive polynomial: x^8 + x^4 + x^3 + x^2 + 1 (0x11D)

Both encoder and decoder are implemented here in pure Python.
The decoder/ version (fec_fast.py) may use numpy for acceleration.
"""

from . import protocol

# ─── GF(256) Arithmetic ───────────────────────────────────────────────

# Pre-computed lookup tables for GF(256) with primitive polynomial 0x11D
_EXP_TABLE = [0] * 512  # exp_table[i] = alpha^i
_LOG_TABLE = [0] * 256   # log_table[x] = i where alpha^i = x


def _init_tables():
    """Initialize GF(256) exp and log lookup tables."""
    x = 1
    for i in range(255):
        _EXP_TABLE[i] = x
        _LOG_TABLE[x] = i
        x <<= 1
        if x & 0x100:
            x ^= protocol.RS_PRIM_POLY
    # Extend exp table for easy modular access
    for i in range(255, 512):
        _EXP_TABLE[i] = _EXP_TABLE[i - 255]


_init_tables()


def gf_mul(a, b):
    """Multiply two elements in GF(256)."""
    if a == 0 or b == 0:
        return 0
    return _EXP_TABLE[_LOG_TABLE[a] + _LOG_TABLE[b]]


def gf_div(a, b):
    """Divide a by b in GF(256). b must be nonzero."""
    if b == 0:
        raise ZeroDivisionError("Division by zero in GF(256)")
    if a == 0:
        return 0
    return _EXP_TABLE[(_LOG_TABLE[a] - _LOG_TABLE[b]) % 255]


def gf_pow(a, n):
    """Raise a to power n in GF(256)."""
    if n == 0:
        return 1
    if a == 0:
        return 0
    return _EXP_TABLE[(_LOG_TABLE[a] * n) % 255]


def gf_inverse(a):
    """Multiplicative inverse in GF(256)."""
    if a == 0:
        raise ZeroDivisionError("No inverse for 0 in GF(256)")
    return _EXP_TABLE[255 - _LOG_TABLE[a]]


# ─── Polynomial Operations over GF(256) ───────────────────────────────

def gf_poly_mul(p, q):
    """Multiply two polynomials over GF(256).
    Polynomials are lists of coefficients, highest degree first."""
    r = [0] * (len(p) + len(q) - 1)
    for i, pi in enumerate(p):
        if pi == 0:
            continue
        for j, qj in enumerate(q):
            if qj == 0:
                continue
            r[i + j] ^= gf_mul(pi, qj)
    return r


def gf_poly_eval(poly, x):
    """Evaluate polynomial at x using Horner's method."""
    result = 0
    for coef in poly:
        result = gf_mul(result, x) ^ coef
    return result


def gf_poly_div(dividend, divisor):
    """Polynomial division over GF(256).
    Returns (quotient, remainder).
    Polynomials are lists of coefficients, highest degree first."""
    if len(divisor) == 0 or all(c == 0 for c in divisor):
        raise ZeroDivisionError("Division by zero polynomial")

    result = list(dividend)
    normalizer = divisor[0]

    for i in range(len(dividend) - len(divisor) + 1):
        coef = gf_div(result[i], normalizer) if normalizer != 1 else result[i]
        result[i] = coef
        if coef == 0:
            continue
        for j in range(1, len(divisor)):
            result[i + j] ^= gf_mul(divisor[j], coef)

    sep = len(dividend) - len(divisor) + 1
    return result[:sep], result[sep:]


# ─── Reed-Solomon Encoder ─────────────────────────────────────────────

# Generator polynomial: g(x) = product((x - alpha^i) for i in 0..2t-1)
# Computed once at module load time.

def _build_generator_poly(nsym):
    """Build RS generator polynomial for nsym parity symbols."""
    g = [1]
    for i in range(nsym):
        g = gf_poly_mul(g, [1, _EXP_TABLE[i]])
    return g


# Default generator for RS(255,223) with 32 parity symbols
_GENERATOR_POLY = _build_generator_poly(protocol.RS_2T)


def rs_encode(message, nsym=None):
    """
    Reed-Solomon encode: append parity bytes to message.

    Args:
        message: bytes or list of ints (length <= RS_K = 223)
        nsym: number of parity symbols (default: RS_2T = 32)

    Returns:
        bytes of length RS_N (255) = message + parity
    """
    if nsym is None:
        nsym = protocol.RS_2T

    msg = list(message)

    # Pad message to RS_K if shorter
    if len(msg) < protocol.RS_K:
        msg = msg + [0] * (protocol.RS_K - len(msg))

    if nsym == protocol.RS_2T:
        gen = _GENERATOR_POLY
    else:
        gen = _build_generator_poly(nsym)

    # Polynomial division: message * x^nsym mod generator
    # Shift message by nsym positions (multiply by x^nsym)
    dividend = msg + [0] * nsym

    # Compute remainder
    for i in range(len(msg)):
        coef = dividend[i]
        if coef == 0:
            continue
        for j in range(1, len(gen)):
            dividend[i + j] ^= gf_mul(gen[j], coef)

    # Parity bytes are the last nsym bytes of dividend
    parity = dividend[len(msg):]

    return bytes(msg) + bytes(parity)


# ─── Reed-Solomon Decoder ─────────────────────────────────────────────

def rs_calc_syndromes(codeword, nsym=None):
    """
    Compute syndromes S_i = C(alpha^i) for i=0..nsym-1.
    If all syndromes are zero, no errors detected.
    """
    if nsym is None:
        nsym = protocol.RS_2T
    syns = []
    for i in range(nsym):
        syns.append(gf_poly_eval(list(codeword), _EXP_TABLE[i]))
    return syns


def _rs_find_error_locator(syndromes):
    """
    Berlekamp-Massey algorithm to find error locator polynomial.

    Uses lowest-degree-first polynomial representation internally:
    [c0, c1, c2, ...] represents c0 + c1*x + c2*x^2 + ...

    Returns:
        error locator polynomial in lowest-degree-first order
    """
    nsym = len(syndromes)
    # C(x) = 1, B(x) = 1
    C = [1]  # current error locator
    B = [1]  # previous error locator
    L = 0
    m = 1
    b = 1

    for n in range(nsym):
        # Compute discrepancy d = S_n + sum(C_i * S_{n-i})
        d = syndromes[n]
        for i in range(1, L + 1):
            if i < len(C):
                d ^= gf_mul(C[i], syndromes[n - i])

        if d == 0:
            m += 1
        elif 2 * L <= n:
            T = list(C)
            # C(x) = C(x) - (d/b) * x^m * B(x)
            coef = gf_div(d, b)
            # x^m * B(x)
            shifted_B = [0] * m + B
            # Ensure same length
            while len(C) < len(shifted_B):
                C.append(0)
            for i in range(len(shifted_B)):
                C[i] ^= gf_mul(coef, shifted_B[i])
            L = n + 1 - L
            B = T
            b = d
            m = 1
        else:
            # C(x) = C(x) - (d/b) * x^m * B(x)
            coef = gf_div(d, b)
            shifted_B = [0] * m + B
            while len(C) < len(shifted_B):
                C.append(0)
            for i in range(len(shifted_B)):
                C[i] ^= gf_mul(coef, shifted_B[i])
            m += 1

    return C


def _rs_find_errors(error_locator):
    """
    Chien search: find error positions from error locator polynomial.

    The error locator is in lowest-degree-first order:
    Lambda(x) = Lambda_0 + Lambda_1*x + Lambda_2*x^2 + ...

    Roots X_j^(-1) of Lambda(x) correspond to error positions j
    where X_j = alpha^j.

    Returns:
        list of error positions
    """
    num_errors = len(error_locator) - 1
    positions = []

    for i in range(protocol.RS_N):
        # Evaluate Lambda(alpha^(-i)) = Lambda(alpha^(255-i))
        xi_inv = _EXP_TABLE[255 - i] if i > 0 else 1
        val = 0
        power = 1  # (xi_inv)^0 = 1
        for coef in error_locator:
            if coef != 0:
                val ^= gf_mul(coef, power)
            power = gf_mul(power, xi_inv)
        if val == 0:
            positions.append(i)

    if len(positions) != num_errors:
        raise ValueError(
            f"Chien search found {len(positions)} roots, expected {num_errors}. "
            "Codeword may have too many errors to correct.")

    return positions


def _rs_find_error_magnitudes(syndromes, error_positions):
    """
    Compute error magnitudes by directly solving the linear system
    derived from syndromes and known error positions.

    For v errors at positions p_0,...,p_{v-1} with magnitudes e_0,...,e_{v-1}:
      S_j = sum(e_i * alpha^(j * p_i)) for j = 0,...,2t-1

    We use the first v equations and solve via Gaussian elimination in GF(256).

    Returns:
        list of (position, magnitude) tuples
    """
    v = len(error_positions)
    if v == 0:
        return []

    # Build matrix: A[j][i] = alpha^(j * p_i) for j=0..v-1, i=0..v-1
    # And RHS: b[j] = S_j
    matrix = []
    rhs = []
    for j in range(v):
        row = []
        for p in error_positions:
            row.append(_EXP_TABLE[(j * p) % 255] if p > 0 else 1)
        matrix.append(row)
        rhs.append(syndromes[j])

    # Gaussian elimination in GF(256)
    for col in range(v):
        # Find pivot
        pivot = None
        for row in range(col, v):
            if matrix[row][col] != 0:
                pivot = row
                break
        if pivot is None:
            raise ValueError("Singular matrix in error magnitude computation")

        # Swap rows
        if pivot != col:
            matrix[col], matrix[pivot] = matrix[pivot], matrix[col]
            rhs[col], rhs[pivot] = rhs[pivot], rhs[col]

        # Eliminate below
        inv_pivot = gf_inverse(matrix[col][col])
        for row in range(col + 1, v):
            if matrix[row][col] == 0:
                continue
            factor = gf_mul(matrix[row][col], inv_pivot)
            for c in range(col, v):
                matrix[row][c] ^= gf_mul(factor, matrix[col][c])
            rhs[row] ^= gf_mul(factor, rhs[col])

    # Back-substitution
    magnitudes = [0] * v
    for row in range(v - 1, -1, -1):
        val = rhs[row]
        for c in range(row + 1, v):
            val ^= gf_mul(matrix[row][c], magnitudes[c])
        magnitudes[row] = gf_div(val, matrix[row][row])

    corrections = []
    for i, pos in enumerate(error_positions):
        if magnitudes[i] != 0:
            corrections.append((pos, magnitudes[i]))

    return corrections


def rs_decode(codeword, nsym=None):
    """
    Reed-Solomon decode: detect and correct errors.

    Args:
        codeword: bytes or list of ints (length RS_N = 255)
        nsym: number of parity symbols (default: RS_2T = 32)

    Returns:
        (corrected_message, num_errors) where:
        - corrected_message is bytes of length RS_K (223)
        - num_errors is number of corrected errors (0 if no errors)

    Raises:
        ValueError: if errors exceed correction capability
    """
    if nsym is None:
        nsym = protocol.RS_2T

    cw = list(codeword)
    if len(cw) < protocol.RS_N:
        cw = cw + [0] * (protocol.RS_N - len(cw))

    # Step 1: Compute syndromes
    syndromes = rs_calc_syndromes(cw, nsym)

    # If all syndromes are zero, no errors
    if all(s == 0 for s in syndromes):
        return bytes(cw[:protocol.RS_K]), 0

    # Step 2: Find error locator polynomial (Berlekamp-Massey)
    error_locator = _rs_find_error_locator(syndromes)

    num_errors = len(error_locator) - 1
    if num_errors > nsym // 2:
        raise ValueError(
            f"Too many errors detected ({num_errors} > {nsym//2}). "
            "Codeword is uncorrectable.")

    # Step 3: Find error positions (Chien search)
    error_positions = _rs_find_errors(error_locator)

    # Step 4: Compute error magnitudes (direct linear solve)
    corrections = _rs_find_error_magnitudes(syndromes, error_positions)

    # Step 5: Apply corrections
    # Polynomial position p maps to array index (N-1-p) because gf_poly_eval
    # treats codeword[0] as coefficient of x^(N-1).
    corrected = list(cw)
    for poly_pos, mag in corrections:
        array_idx = protocol.RS_N - 1 - poly_pos
        if 0 <= array_idx < len(corrected):
            corrected[array_idx] ^= mag

    # Verify: recompute syndromes on corrected codeword
    verify_syns = rs_calc_syndromes(corrected, nsym)
    if not all(s == 0 for s in verify_syns):
        raise ValueError("Correction failed verification. Codeword uncorrectable.")

    return bytes(corrected[:protocol.RS_K]), num_errors

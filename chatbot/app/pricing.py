from dataclasses import dataclass, field


# Prices in GHS
PRICE_BW_A4 = 0.50
PRICE_COLOUR_A4 = 2.00
PRICE_BW_A3 = 1.00
PRICE_COLOUR_A3 = 4.00
PRICE_BINDING = 5.00
PRICE_LAMINATION_PER_PAGE = 3.00
PRICE_SPIRAL_BINDING = 8.00


@dataclass
class QuoteBreakdown:
    print_type: str          # "bw" | "colour"
    paper_size: str          # "a4" | "a3"
    pages: int
    copies: int
    binding: bool
    spiral: bool
    lamination: bool
    lamination_pages: int = 0
    extras_cost: float = 0.0
    subtotal: float = 0.0
    total: float = 0.0
    line_items: list[str] = field(default_factory=list)


def calculate_quote(
    print_type: str,
    paper_size: str,
    pages: int,
    copies: int,
    binding: bool = False,
    spiral: bool = False,
    lamination: bool = False,
) -> QuoteBreakdown:
    q = QuoteBreakdown(
        print_type=print_type,
        paper_size=paper_size,
        pages=pages,
        copies=copies,
        binding=binding,
        spiral=spiral,
        lamination=lamination,
    )

    if print_type == "colour":
        unit = PRICE_COLOUR_A3 if paper_size == "a3" else PRICE_COLOUR_A4
    else:
        unit = PRICE_BW_A3 if paper_size == "a3" else PRICE_BW_A4

    print_cost = unit * pages * copies
    size_label = paper_size.upper()
    type_label = "Colour" if print_type == "colour" else "B&W"
    q.line_items.append(
        f"{type_label} {size_label} × {pages} pages × {copies} cop{'y' if copies == 1 else 'ies'} = GHS {print_cost:.2f}"
    )

    if binding:
        q.extras_cost += PRICE_BINDING
        q.line_items.append(f"Binding = GHS {PRICE_BINDING:.2f}")

    if spiral:
        q.extras_cost += PRICE_SPIRAL_BINDING
        q.line_items.append(f"Spiral binding = GHS {PRICE_SPIRAL_BINDING:.2f}")

    if lamination:
        lam_pages = pages * copies
        lam_cost = PRICE_LAMINATION_PER_PAGE * lam_pages
        q.lamination_pages = lam_pages
        q.extras_cost += lam_cost
        q.line_items.append(f"Lamination × {lam_pages} pages = GHS {lam_cost:.2f}")

    q.subtotal = print_cost + q.extras_cost
    q.total = q.subtotal
    return q


def format_quote(q: QuoteBreakdown, order_ref: str) -> str:
    lines = [f"*Order #{order_ref} — Quote*", ""]
    lines.extend(q.line_items)
    lines.append("─────────────────")
    lines.append(f"*TOTAL: GHS {q.total:.2f}*")
    return "\n".join(lines)

"""Boord block ids that changed after the historical workbooks were made.

The source workbooks name their columns with the block ids that were current
when they were prepared. Boord's register has moved since, and a historical
row keyed to an id that no longer exists is not an error anybody sees - it
simply never joins to a block, so the block quietly shows no history at all
while every total still adds up.

So both import scripts pass every block id through RENAMES below.

WHAT CHANGED, and how it was established
----------------------------------------
Three pairs lost their `a`/`b` lettering, with the SECOND of each pair
taking the bare number:

    old 10a (Mauritius, 512 trees, 2.5 ha)  ->  new 10b   Block 10b-Mau
    old 10b (Early Delight, 512, 2.4 ha)    ->  new 10    Block 10-ED
    old 17a (Mauritius, 584, 2.8 ha)        ->  new 17b   Block 17b-Mau
    old 17b (Early Delight, 584, 2.8 ha)    ->  new 17    Block 17-ED
    old 19a (Mauritius, 358, 1.7 ha)        ->  new 19b   Block 19b-Mau
    old 19b (Early Delight, 358, 1.7 ha)    ->  new 19    Block 19-ED

This mapping is deliberately NOT read off the id. It is read off variety,
tree count and hectares, which agree on all six and are what actually
identify a piece of orchard. It is worth being explicit that this is the
opposite of what the ids suggest: the bare `10` is the old `10b`, not the
old `10a`. Hectares settle it - new `10` is 2.4 ha, which is old `10b`'s;
old `10a` was 2.5 ha and is now `10b`.

Mapping by id instead would put each Mauritius block's six seasons onto an
Early Delight block of a different size. Nothing would error; the season
totals would even still be right. Only kg/ha, kg/tree and the variety
charts would be wrong, and only against a past nobody can check by eye.

8a and 8b are untouched - that pair kept its lettering.

WHEN THIS NEEDS REVISITING
--------------------------
This is a record of one farm's register at one moment, not a general
mechanism. If Boord's ids move again, add to this map rather than editing
the workbooks: the workbooks are the farm's own records and should keep
saying what they said when they were written.

`scripts/check_block_ids.py` reports any imported id that is not in Boord's
register, which is how a future rename gets noticed rather than silently
producing a block with no history.
"""

# old id in the workbook -> id in Boord's register today
RENAMES = {
    "10a": "10b",
    "10b": "10",
    "17a": "17b",
    "17b": "17",
    "19a": "19b",
    "19b": "19",
}


def rename(block_id):
    """The current id for a block id as written in a source workbook.

    Anything not in RENAMES is returned unchanged, so ids that never moved
    (7, 8a, 8b, 9, 11, ...) need no entry.
    """
    if block_id is None:
        return None
    return RENAMES.get(str(block_id), str(block_id))

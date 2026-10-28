-- rebate_program is carried through and nothing downstream reads it, which is
-- worth saying out loud rather than leaving as a dangling column.
--
-- Three programs exist in the corpus and one of them is annual. The generator
-- anchors every volume rebate on the quarter end regardless, so a vendor on an
-- annual program produces the same credit timing here as a vendor on a
-- quarterly one. A derived flag claiming otherwise was written and deleted,
-- because the column would have implied a relationship the data does not hold.
-- The anchor is the thing to fix, in the generator, on the reconciliation day.
select
    vendor_id,
    vendor_name,
    rebate_program
from {{ source('raw', 'vendors') }}

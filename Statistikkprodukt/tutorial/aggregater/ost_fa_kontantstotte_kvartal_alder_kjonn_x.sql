select
    aar_kvartal_besk_kort
    , kjonn_flertall_besk
    , ti_aar_gruppe_besk
    , sum(kontantstotte_antall_mottakere) as kontantstotte_antall_mottakere
from obt_fa_kontantstotte_kvartal
group by
    aar_kvartal_besk_kort
    , kjonn_flertall_besk
    , ti_aar_gruppe_besk
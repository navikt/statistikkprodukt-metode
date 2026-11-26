select periode, alder, sum(antall) as antall
from obt_befolkning
group by periode, alder
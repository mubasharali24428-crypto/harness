FROM harness-engine-worker:s12
USER root
COPY rc.sh /probe/rc.sh
RUN mv /usr/bin/bash /usr/bin/bash.real \
    && printf '#!/bin/sh\nexec /usr/bin/bash.real --rcfile /probe/rc.sh "$@"\n' > /usr/bin/bash \
    && chmod 755 /usr/bin/bash
USER 10001:10001

# curatarr
# Copyright (C) 2026 OrchestratedChaos
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""
Streaming-service categorization for external recommendations - buckets a
list of scored recommendations into "available on your services",
"available on other services", or "acquire" (not streaming anywhere),
using TMDB watch-provider data.

Extracted out of recommenders/external.py (PR2 architecture decomposition,
step 3 - see CHANGELOG). Depends on recommenders/huntarr.py's
get_watch_providers() - the TMDB watch-provider lookup helper Sequel
Huntarr originally introduced (see that module's docstring for why it
lives there rather than a separate shared "TMDB lookups" module).

This is a pure relocation: categorize_by_streaming_service() below is
byte-for-byte identical to its former recommenders/external.py definition
(see that module's git history for the pre-move version) - only the
import path changed. recommenders/external.py re-exports it (it's still
called from several of that module's own functions, so it stays a normal
top-level import there, not an __all__-only re-export) so existing
callers/tests (many of which `@patch("recommenders.external.
categorize_by_streaming_service")`) keep working unchanged.
"""

from typing import Any, Dict, List

from recommenders.huntarr import get_watch_providers


def categorize_by_streaming_service(
    recommendations: List[Dict], tmdb_api_key: str, user_services: List[str], media_type: str = "movie"
) -> Dict:
    """
    Categorize recommendations by streaming availability.
    Each item gets streaming_services, rent_services, buy_services, and on_user_services added.

    Returns dict: {
        'user_services': {service_name: [items]},
        'other_services': {service_name: [items]},
        'acquire': [items],
        'all_items': [all items sorted by score with streaming info]
    }
    """
    result: Dict[str, Any] = {"user_services": {}, "other_services": {}, "acquire": [], "all_items": []}

    for item in recommendations:
        tmdb_id = item["tmdb_id"]
        providers = get_watch_providers(tmdb_api_key, tmdb_id, media_type)

        # Attach streaming info to item
        streaming = providers.get("streaming", [])
        item["streaming_services"] = streaming
        item["rent_services"] = providers.get("rent", [])
        item["buy_services"] = providers.get("buy", [])
        item["on_user_services"] = [s for s in streaming if s in user_services]

        # Add to all_items for flat display
        result["all_items"].append(item)

        if not streaming:
            # Not available on any subscription streaming service
            result["acquire"].append(item)
        else:
            # Check which services have it - add to FIRST matching service only
            # Priority: user's services first, then other services
            placed = False

            # First try user's services
            for service in streaming:
                if service in user_services:
                    if service not in result["user_services"]:
                        result["user_services"][service] = []
                    result["user_services"][service].append(item)
                    placed = True
                    break  # Only add to ONE service

            # If not on user's services, add to first other service
            if not placed:
                for service in streaming:
                    if service not in user_services:
                        if service not in result["other_services"]:
                            result["other_services"][service] = []
                        result["other_services"][service].append(item)
                        break  # Only add to ONE service

    # Sort all_items by score (highest first)
    result["all_items"].sort(key=lambda x: x.get("score", 0), reverse=True)

    return result

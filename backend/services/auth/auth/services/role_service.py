"""
Roles & permissions per organisation.

Effective permissions for a member, in order:
1. owner (super_admin): everything, always
2. candidate (student): the fixed candidate permissions
3. a custom role (membership.role_id): that role's permissions
4. a built-in role the organisation edited (org_roles row keyed by the role)
5. otherwise the code defaults for the built-in role
"""
import re
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from fastapi import HTTPException
from sqlmodel import Session, col, func, select

from auth.api_models.role_api_models import (
    MyPermissionsRead, PermissionInfoRead, RoleCreate, RoleRead, RoleUpdate,
)
from auth.core.permissions import (
    CANDIDATE_PERMISSIONS, CATALOG, DEFAULT_ROLE_GRANTS, P, REACH_ORDER, Grants,
    decode, encode, expand, grants_for_role,
)
from auth.database.schema.membership.membership_db import OrgMembership
from auth.database.schema.role.role_db import OrgRole
from auth.database.schema.user.enums import MembershipStatus, UserRole

SYSTEM_ROLES: dict[str, tuple[str, str]] = {
    "admin": ("Admin", "Runs the organisation day to day: everything except the plan and roles."),
    "teacher": ("Teacher", "Writes questions and builds exams for their assigned subjects; marks and sees those results."),
    "supervisor": ("Supervisor", "Approves exams for their assigned subjects and sees their violations."),
    "staff": ("Staff", "No permissions until you give them some; anything given is limited to what they're assigned."),
}
OWNER_KEY = "super_admin"
RESERVED_KEYS: frozenset[str] = frozenset({*SYSTEM_ROLES, OWNER_KEY, "student"})
COUNTED_STATUSES = (MembershipStatus.ACTIVE, MembershipStatus.PENDING)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _role_value(role: UserRole | str) -> str:
    return role.value if isinstance(role, UserRole) else str(role)


class RoleService:

    # ------------------------------------------------------------ resolving

    @staticmethod
    def resolve_grants(session: Session, membership: OrgMembership) -> Grants:
        """Effective (expanded) permissions of one member."""
        role = _role_value(membership.role)
        if role in (OWNER_KEY, "student"):
            return grants_for_role(role)

        row: OrgRole | None = None
        if membership.role_id is not None:
            row = session.get(OrgRole, membership.role_id)
        else:
            row = RoleService._system_row(session, membership.org_id, role)
        if row is not None and row.permissions is not None:
            return expand(decode(row.permissions) or {})
        return grants_for_role(role)

    @staticmethod
    def token_perms(session: Session, membership: OrgMembership | None) -> dict[str, str] | None:
        """The "perms" claim for an access token (None when there's no membership)."""
        if membership is None:
            return None
        return encode(RoleService.resolve_grants(session, membership))

    @staticmethod
    def _system_row(session: Session, org_id: UUID, key: str) -> OrgRole | None:
        return session.exec(
            select(OrgRole).where(OrgRole.org_id == org_id, OrgRole.key == key, OrgRole.is_system)
        ).first()

    # -------------------------------------------------------------- reading

    @staticmethod
    def catalog() -> list[PermissionInfoRead]:
        return [
            PermissionInfoRead(
                key=p.value,
                group=info.group,
                label=info.label,
                description=info.description,
                scoped=info.scoped,
                implies=[(e[0] if isinstance(e, tuple) else e).value for e in info.implies],
                candidate_only=p in CANDIDATE_PERMISSIONS,
            )
            for p, info in CATALOG.items()
        ]

    @staticmethod
    def _member_counts(session: Session, org_id: UUID) -> tuple[dict[str, int], dict[UUID, int]]:
        rows = session.exec(
            select(OrgMembership.role, OrgMembership.role_id, func.count())
            .where(OrgMembership.org_id == org_id, col(OrgMembership.status).in_(COUNTED_STATUSES))
            .group_by(col(OrgMembership.role), col(OrgMembership.role_id))
        ).all()
        by_base: dict[str, int] = {}
        by_custom: dict[UUID, int] = {}
        for role, role_id, n in rows:
            if role_id is None:
                key = _role_value(role)
                by_base[key] = by_base.get(key, 0) + int(n)
            else:
                by_custom[role_id] = by_custom.get(role_id, 0) + int(n)
        return by_base, by_custom

    @staticmethod
    def list(session: Session, org_id: UUID) -> list[RoleRead]:
        rows: list[OrgRole] = list(
            session.exec(select(OrgRole).where(OrgRole.org_id == org_id).order_by(col(OrgRole.name))).all()
        )
        system_rows: dict[str, OrgRole] = {r.key: r for r in rows if r.is_system}
        by_base, by_custom = RoleService._member_counts(session, org_id)

        out: list[RoleRead] = [
            RoleRead(
                key=OWNER_KEY, id=None, name="Owner", description="Created the organisation. Can do everything; can't be changed.",
                base_role=OWNER_KEY, is_system=True, is_customised=False, editable=False,
                permissions=encode(DEFAULT_ROLE_GRANTS[OWNER_KEY]), member_count=by_base.get(OWNER_KEY, 0),
            )
        ]
        for key, (name, description) in SYSTEM_ROLES.items():
            row = system_rows.get(key)
            customised = row is not None and row.permissions is not None
            out.append(RoleRead(
                key=key,
                id=row.id if row else None,
                name=name,
                description=(row.description if row and row.description else description),
                base_role=key,
                is_system=True,
                is_customised=customised,
                editable=True,
                permissions=dict(row.permissions) if customised and row else encode(DEFAULT_ROLE_GRANTS[key]),
                member_count=by_base.get(key, 0),
                updated_at=row.updated_at if row else None,
            ))
        for row in rows:
            if row.is_system:
                continue
            out.append(RoleRead(
                key=row.key, id=row.id, name=row.name, description=row.description, base_role=row.base_role,
                is_system=False, is_customised=False, editable=True,
                permissions=dict(row.permissions or {}), member_count=by_custom.get(row.id, 0),
                updated_at=row.updated_at,
            ))
        return out

    @staticmethod
    def get(session: Session, org_id: UUID, key: str) -> RoleRead:
        for role in RoleService.list(session, org_id):
            if role.key == key:
                return role
        raise HTTPException(status_code=404, detail="Role not found.")

    @staticmethod
    def my_permissions(session: Session, membership: OrgMembership) -> MyPermissionsRead:
        role = _role_value(membership.role)
        key, name = role, SYSTEM_ROLES.get(role, ("Owner" if role == OWNER_KEY else role.title(), ""))[0]
        if membership.role_id is not None:
            row = session.get(OrgRole, membership.role_id)
            if row is not None:
                key, name = row.key, row.name
        return MyPermissionsRead(
            role=role, role_key=key, role_name=name,
            permissions=encode(RoleService.resolve_grants(session, membership)),
        )

    # ------------------------------------------------------------ validating

    @staticmethod
    def _validate(raw: dict[str, Any], actor_grants: Grants, actor_is_owner: bool) -> dict[str, str]:
        """Known permissions, valid reach, no candidate permissions, nothing beyond the actor."""
        valid_keys: set[str] = {p.value for p in P}
        unknown = [k for k in raw if k not in valid_keys]
        if unknown:
            raise HTTPException(status_code=422, detail={"message": "Unknown permissions.", "permissions": unknown})
        cleaned: dict[str, str] = {}
        for key, reach in raw.items():
            perm = P(key)
            if reach not in REACH_ORDER:
                raise HTTPException(status_code=422, detail=f"'{key}': reach must be all, assigned or own.")
            if reach != "all" and not CATALOG[perm].scoped:
                raise HTTPException(status_code=422, detail=f"'{key}' doesn't support '{reach}'; use all.")
            if perm in CANDIDATE_PERMISSIONS:
                raise HTTPException(status_code=422, detail=f"'{key}' is only for candidates.")
            cleaned[key] = str(reach)

        if not actor_is_owner:
            wanted: Grants = expand(decode(cleaned) or {})
            beyond = [
                p.value for p, r in wanted.items()
                if p not in actor_grants or REACH_ORDER[r] > REACH_ORDER[actor_grants[p]]
            ]
            if beyond:
                raise HTTPException(
                    status_code=403,
                    detail={"message": "You can't give permissions you don't have.", "permissions": sorted(beyond)},
                )
        return cleaned

    @staticmethod
    def _slug(session: Session, org_id: UUID, name: str) -> str:
        base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:50] or "role"
        if base in RESERVED_KEYS:
            base = f"{base}-role"
        key, n = base, 2
        while session.exec(select(OrgRole.id).where(OrgRole.org_id == org_id, OrgRole.key == key)).first():
            key, n = f"{base}-{n}", n + 1
        return key

    # -------------------------------------------------------------- writing

    @staticmethod
    def create(
        session: Session, org_id: UUID, payload: RoleCreate, actor_id: UUID, actor_grants: Grants, actor_is_owner: bool
    ) -> RoleRead:
        clash = session.exec(
            select(OrgRole.id).where(OrgRole.org_id == org_id, func.lower(OrgRole.name) == payload.name.lower())
        ).first()
        if clash or payload.name.lower() in {n.lower() for n, _ in SYSTEM_ROLES.values()} | {"owner"}:
            raise HTTPException(status_code=409, detail=f"A role named '{payload.name}' already exists.")
        row = OrgRole(
            org_id=org_id,
            key=RoleService._slug(session, org_id, payload.name),
            name=payload.name,
            description=payload.description,
            base_role=UserRole.STAFF.value,
            is_system=False,
            permissions=RoleService._validate(payload.permissions, actor_grants, actor_is_owner),
            created_by=actor_id,
        )
        session.add(row)
        session.commit()
        return RoleService.get(session, org_id, row.key)

    @staticmethod
    def update(
        session: Session, org_id: UUID, key: str, payload: RoleUpdate,
        actor_id: UUID, actor_grants: Grants, actor_is_owner: bool,
    ) -> RoleRead:
        if key == OWNER_KEY:
            raise HTTPException(status_code=400, detail="The owner role can't be changed.")
        data = payload.model_dump(exclude_unset=True)

        if key in SYSTEM_ROLES:
            if data.get("name"):
                raise HTTPException(status_code=400, detail="Built-in roles can't be renamed; copy one instead.")
            row = RoleService._system_row(session, org_id, key)
            if row is None:
                row = OrgRole(
                    org_id=org_id, key=key, name=SYSTEM_ROLES[key][0], base_role=key,
                    is_system=True, permissions=None, created_by=actor_id,
                )
        else:
            row = session.exec(
                select(OrgRole).where(OrgRole.org_id == org_id, OrgRole.key == key, col(OrgRole.is_system).is_(False))
            ).first()
            if row is None:
                raise HTTPException(status_code=404, detail="Role not found.")
            if data.get("name") and data["name"] != row.name:
                clash = session.exec(
                    select(OrgRole.id).where(
                        OrgRole.org_id == org_id, func.lower(OrgRole.name) == data["name"].lower(), OrgRole.id != row.id,
                    )
                ).first()
                if clash:
                    raise HTTPException(status_code=409, detail=f"A role named '{data['name']}' already exists.")
                row.name = data["name"]

        if "description" in data:
            row.description = data["description"]
        if data.get("permissions") is not None:
            row.permissions = RoleService._validate(data["permissions"], actor_grants, actor_is_owner)
        row.updated_at = _now()
        session.add(row)
        session.commit()
        return RoleService.get(session, org_id, key)

    @staticmethod
    def reset(session: Session, org_id: UUID, key: str) -> RoleRead:
        """Back to the code defaults (built-in roles only)."""
        if key not in SYSTEM_ROLES:
            raise HTTPException(status_code=400, detail="Only built-in roles can be reset.")
        row = RoleService._system_row(session, org_id, key)
        if row is not None:
            row.permissions = None
            row.updated_at = _now()
            session.add(row)
            session.commit()
        return RoleService.get(session, org_id, key)

    @staticmethod
    def delete(session: Session, org_id: UUID, key: str) -> None:
        row = session.exec(
            select(OrgRole).where(OrgRole.org_id == org_id, OrgRole.key == key, col(OrgRole.is_system).is_(False))
        ).first()
        if row is None:
            raise HTTPException(status_code=404, detail="Role not found." if key not in RESERVED_KEYS else "Built-in roles can't be deleted.")
        in_use = session.exec(
            select(OrgMembership.id).where(OrgMembership.role_id == row.id).limit(1)
        ).first()
        if in_use is not None:
            raise HTTPException(status_code=409, detail="Move the people in this role to another role first.")
        session.delete(row)
        session.commit()

    @staticmethod
    def assign(
        session: Session, org_id: UUID, user_id: UUID, key: str,
        actor_id: UUID, actor_role: UserRole, actor_grants: Grants,
    ) -> MyPermissionsRead:
        """Give a staff member a role. Only the owner manages admins; nobody changes the owner."""
        membership: OrgMembership | None = session.exec(
            select(OrgMembership).where(OrgMembership.user_id == user_id, OrgMembership.org_id == org_id)
        ).first()
        if membership is None:
            raise HTTPException(status_code=404, detail="User not found in this organization.")
        current = _role_value(membership.role)
        if current == "student":
            raise HTTPException(status_code=400, detail="Candidates can't be given staff roles.")
        if current == OWNER_KEY or key == OWNER_KEY:
            raise HTTPException(status_code=403, detail="The owner's role can't be changed or given.")
        if user_id == actor_id:
            raise HTTPException(status_code=400, detail="You can't change your own role.")
        is_owner = actor_role == UserRole.SUPER_ADMIN
        if (current == "admin" or key == "admin") and not is_owner:
            raise HTTPException(status_code=403, detail="Only the owner can make or change admins.")

        if key in SYSTEM_ROLES:
            base, role_id = key, None
            target_grants: Grants = RoleService.resolve_grants(
                session, OrgMembership(user_id=user_id, org_id=org_id, role=UserRole(key))
            )
        else:
            row = session.exec(
                select(OrgRole).where(OrgRole.org_id == org_id, OrgRole.key == key, col(OrgRole.is_system).is_(False))
            ).first()
            if row is None:
                raise HTTPException(status_code=404, detail="Role not found.")
            base, role_id = row.base_role, row.id
            target_grants = expand(decode(row.permissions or {}) or {})

        if not is_owner:
            beyond = [
                p.value for p, r in target_grants.items()
                if p not in actor_grants or REACH_ORDER[r] > REACH_ORDER[actor_grants[p]]
            ]
            if beyond:
                raise HTTPException(
                    status_code=403,
                    detail={"message": "That role has permissions you don't have.", "permissions": sorted(beyond)},
                )

        membership.role = UserRole(base)
        membership.role_id = role_id
        membership.updated_at = _now()
        session.add(membership)
        session.commit()
        session.refresh(membership)
        return RoleService.my_permissions(session, membership)

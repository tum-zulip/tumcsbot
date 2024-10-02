from dataclasses import dataclass
from typing import Any, Literal, Generator, cast
from time import time
import requests
import yaml

"""
https://review.campus.tum.de/RSYSTEM/co/public/app/openapi/course
"""


@dataclass
class Title:
    language: str
    title: str


@dataclass
class Course:
    uid: str
    courseClassificationKey: str
    courseTypeKey: str
    instructionLanguages: list[str]
    mainLanguageOfInstruction: str
    semesterHours: int
    semesterKey: str
    titles: list[Title]

    def __post_init__(self) -> None:
        self.instructionLanguages = [l.lower() for l in self.instructionLanguages if l]
        self.mainLanguageOfInstruction = self.mainLanguageOfInstruction.lower()

    @property
    def title(self) -> str:
        for t in self.titles:
            if t.language == self.mainLanguageOfInstruction:
                return t.title
        raise ValueError(
            f"No title found for the main language of instruction: {self.mainLanguageOfInstruction}"
        )

    def __str__(self) -> str:
        return f"Course({self.title}, uid={self.uid}, semesterKey={self.semesterKey}, semesterHours={self.semesterHours}, instructionLanguages={self.instructionLanguages})"

    @classmethod
    def from_co_response(cls, r: dict[str, Any]) -> "Course":
        titles = [
            Title(language=lang, title=title)
            for lang, title in r.get("title", {}).get("value", {}).items()
        ]
        del r["title"]
        r["titles"] = titles

        # delete additional keys
        for k in list(r.keys()):
            if k not in cls.__annotations__:
                del r[k]

        return cls(**r)


@dataclass
class Registration:
    uid: str
    personUid: str
    courseUid: str

    @classmethod
    def from_co_response(cls, r: dict[str, Any]) -> "Registration":
        # delete additional keys
        for k in list(r.keys()):
            if k not in cls.__annotations__:
                del r[k]

        return cls(**r)


@dataclass
class Person:
    uid: str
    givenName: str
    surname: str
    email: str

    @classmethod
    def from_co_response(cls, r: dict[str, Any]) -> "Person":
        return cls(**r)


class CampusOnlineClient:

    def __init__(self, base_url: str):
        self.base_url = base_url
        self._token: str | None = None
        self._token_expires = 0

        with open("api.yml") as f:
            yml = yaml.safe_load(f.read())

        self._client_id = yml["Client-ID"]
        self._client_secret = yml["Client-Secret"]

    def _ensure_authentication(self) -> None:
        if self._token is not None and self._token_expires > time():
            return

        response = self._request(
            "POST",
            "/public/sec/auth/realms/CAMPUSonline_SP/protocol/openid-connect/token",
            {
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "grant_type": "client_credentials",
            },
        )

        # todo: handle errors

        self._token = response["access_token"]
        self._token_expires = (
            time() + response["expires_in"] - 10
        )  # 10 seconds buffer, so we don't accidentally use an expired token

    def _request(
        self, method: Literal["GET", "POST"], endpoint: str, params: dict[str, Any]
    ) -> dict[str, Any]:
        url = self.base_url + endpoint
        headers = {}
        if self._token is not None:
            headers["Authorization"] = f"Bearer {self._token}"

        if method == "POST":
            response = requests.request(method, url, data=params, headers=headers)
        else:
            response = requests.request(method, url, params=params, headers=headers)
        response.raise_for_status()
        return cast(dict[str, Any], response.json())

    def request(
        self, method: Literal["GET", "POST"], endpoint: str, params: dict[str, Any]
    ) -> dict[str, Any]:
        self._ensure_authentication()
        return self._request(method, endpoint, params)

    def courses(
        self,
        year: int,
        semester: Literal["W", "S"],
        course_code: str | None = None,
        only_elearning_courses: bool | None = None,
        title: str | None = None,
        type_keys: list[str] | None = None,
    ) -> Generator[Course, None, None]:
        """
        https://campus.tum.de/tumonline/co/public/app/openapi/course
        """
        body: dict[str, Any] = {"semester_key": f"{year}{semester}"}
        for key, value in {
            "courseCode": course_code,
            "only_elearning_courses": only_elearning_courses,
            "title": title,
            "type_keys": type_keys,
        }.items():
            if value is not None:
                body[key] = value

        while True:
            courses = self.request("GET", "/co-tm-core/course/api/courses", body)
            yield from [Course.from_co_response(c) for c in courses["items"]]
            if "nextCursor" not in courses:
                break
            body["cursor"] = courses["nextCursor"]

    def registrations(
        self, course_uids: str | list[str]
    ) -> Generator[Registration, None, None]:
        if isinstance(course_uids, str):
            course_uids = [course_uids]

        body = {"course_uids": course_uids}
        while True:
            registrations = self.request(
                "GET", "/co-tm-core/course/api/registrations", body
            )
            yield from [
                Registration.from_co_response(r) for r in registrations["items"]
            ]
            if "nextCursor" not in registrations:
                break
            body["cursor"] = registrations["nextCursor"]

    def persons(
        self,
        *,
        person_group_key: Literal["STUDENT", "EMPLOYEE", "EXTPERS"],
        person_uids: str | list[str] | None = None,
        surname_like: str | None = None,
        given_name_like: str | None = None,
        email: str | list[str] | None = None,
    ) -> Generator[Person, None, None]:
        body = {}
        for key, value in {
            "surname_like": surname_like,
            "given_name_like": given_name_like,
            "email": email,
            "person_uids": person_uids,
            "person_group_key": person_group_key,
        }.items():
            if value is not None:
                if key in ["email", "person_uids"]:
                    if isinstance(value, str):
                        value = [value]
                body[key] = value

        if len(body) == 0:
            raise ValueError("At least one parameter must be set")

        body["claim"] = [
            "CO_CLAIM_NAME",
            "CO_CLAIM_EMAIL",
            "CO_CLAIM_PERSON_UID",
        ]

        while True:
            persons = self.request("GET", "/co-brm-core/pers/api/personal-claims", body)
            yield from [Person.from_co_response(p) for p in persons["items"]]
            if "nextCursor" not in persons:
                break
            body["cursor"] = persons["nextCursor"]

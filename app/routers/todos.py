"""待办清单接口：全部要求登录，且只能操作自己的数据。"""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, HTTPException, Response, status

from ..deps import CurrentUser, DbConn
from ..schemas import TodoCreate, TodoOut, TodoUpdate
from ..security import to_iso, utcnow

router = APIRouter(prefix="/todos", tags=["待办清单"])


def _to_todo(row: sqlite3.Row) -> TodoOut:
    return TodoOut(
        id=row["id"],
        title=row["title"],
        done=bool(row["done"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _load_own_todo(conn: sqlite3.Connection, todo_id: int, user_id: int) -> sqlite3.Row:
    row = conn.execute(
        "SELECT id, title, done, created_at, updated_at FROM todos WHERE id = ? AND user_id = ?",
        (todo_id, user_id),
    ).fetchone()
    if row is None:
        # 别人的待办同样返回 404，避免暴露 id 是否存在。
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="待办不存在")
    return row


@router.get("", response_model=list[TodoOut], summary="查询我的待办")
def list_todos(current_user: CurrentUser, conn: DbConn) -> list[TodoOut]:
    rows = conn.execute(
        """
        SELECT id, title, done, created_at, updated_at
        FROM todos
        WHERE user_id = ?
        ORDER BY done ASC, id DESC
        """,
        (current_user["id"],),
    ).fetchall()
    return [_to_todo(row) for row in rows]


@router.post("", response_model=TodoOut, status_code=status.HTTP_201_CREATED, summary="新增待办")
def create_todo(payload: TodoCreate, current_user: CurrentUser, conn: DbConn) -> TodoOut:
    now = to_iso(utcnow())
    cursor = conn.execute(
        """
        INSERT INTO todos (user_id, title, done, created_at, updated_at)
        VALUES (?, ?, 0, ?, ?)
        """,
        (current_user["id"], payload.title, now, now),
    )
    conn.commit()
    row = conn.execute(
        "SELECT id, title, done, created_at, updated_at FROM todos WHERE id = ?",
        (cursor.lastrowid,),
    ).fetchone()
    assert row is not None
    return _to_todo(row)


@router.patch("/{todo_id}", response_model=TodoOut, summary="修改待办内容或完成状态")
def update_todo(
    todo_id: int,
    payload: TodoUpdate,
    current_user: CurrentUser,
    conn: DbConn,
) -> TodoOut:
    row = _load_own_todo(conn, todo_id, current_user["id"])

    title = payload.title if payload.title is not None else row["title"]
    done = int(payload.done) if payload.done is not None else row["done"]

    if title == row["title"] and done == row["done"]:
        return _to_todo(row)  # 没有任何变化就不写库

    conn.execute(
        "UPDATE todos SET title = ?, done = ?, updated_at = ? WHERE id = ? AND user_id = ?",
        (title, done, to_iso(utcnow()), todo_id, current_user["id"]),
    )
    conn.commit()
    return _to_todo(_load_own_todo(conn, todo_id, current_user["id"]))


@router.delete(
    "/{todo_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="删除待办",
    response_class=Response,
)
def delete_todo(todo_id: int, current_user: CurrentUser, conn: DbConn) -> Response:
    _load_own_todo(conn, todo_id, current_user["id"])
    conn.execute(
        "DELETE FROM todos WHERE id = ? AND user_id = ?",
        (todo_id, current_user["id"]),
    )
    conn.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
